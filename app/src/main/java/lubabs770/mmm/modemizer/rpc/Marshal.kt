package lubabs770.mmm.modemizer.rpc

import android.content.Intent
import android.os.Bundle
import lubabs770.mmm.modemizer.util.B64
import org.json.JSONArray
import org.json.JSONObject

/** Somewhere to park a Java object the client can't be handed directly. */
interface HandleSink {
    fun handle(value: Any): Long
}

/**
 * Translation between Java values and the wire's JSON.
 *
 * Plain values (null, booleans, numbers, strings, byte arrays, and collections of
 * those) cross as JSON. Anything else becomes a handle the client keeps and passes
 * back. When no [HandleSink] is available — events stored in a durable backlog outlive
 * the session their handles would belong to — such a value is replaced by an opaque
 * marker instead, so a persisted event is always self-contained JSON.
 */
object Marshal {

    const val KEY_HANDLE = "\$h"
    const val KEY_B64 = "\$b64"
    const val KEY_CLASS = "\$cls"
    const val KEY_OPAQUE = "\$opaque"
    const val KEY_POJO = "\$pojo"

    // --------------------------------------------------------------- outbound

    fun toJson(value: Any?, sink: HandleSink?): Any {
        return when (value) {
            null, JSONObject.NULL -> JSONObject.NULL
            is Boolean -> value
            is String -> value
            is Char -> value.toString()
            is Int, is Long, is Short, is Byte -> (value as Number).toLong()
            is Float, is Double -> (value as Number).toDouble()
            is ByteArray -> JSONObject().put(KEY_B64, B64.encode(value))
            is Collection<*> -> JSONArray().also { arr -> value.forEach { arr.put(toJson(it, sink)) } }
            is Map<*, *> -> JSONObject().also { obj ->
                value.forEach { (k, v) -> obj.put(k.toString(), toJson(v, sink)) }
            }
            is Class<*> -> JSONObject().put(KEY_CLASS, value.name)
            else -> when {
                value.javaClass.isArray -> arrayToJson(value, sink)
                value is Enum<*> -> handleJson(value, sink).put("name", value.name)
                else -> handleJson(value, sink)
            }
        }
    }

    private fun arrayToJson(array: Any, sink: HandleSink?): JSONArray {
        val length = java.lang.reflect.Array.getLength(array)
        val out = JSONArray()
        for (i in 0 until length) out.put(toJson(java.lang.reflect.Array.get(array, i), sink))
        return out
    }

    private fun handleJson(value: Any, sink: HandleSink?): JSONObject {
        val cls = value.javaClass.name
        if (sink == null) {
            return JSONObject().put(KEY_OPAQUE, cls).put("repr", safeToString(value))
        }
        return JSONObject().put(KEY_HANDLE, sink.handle(value)).put("cls", cls)
    }

    private fun safeToString(value: Any): String =
        runCatching { value.toString() }.getOrElse { "<${value.javaClass.name}>" }

    // ---------------------------------------------------------------- inbound

    /** [resolve] turns a handle back into the object the session holds for it. */
    fun fromJson(json: Any?, resolve: (Long) -> Any): Any? = when (json) {
        null, JSONObject.NULL -> null
        is JSONObject -> when {
            json.has(KEY_HANDLE) -> resolve(json.getLong(KEY_HANDLE))
            json.has(KEY_B64) -> B64.decode(json.getString(KEY_B64))
            json.has(KEY_CLASS) -> Class.forName(json.getString(KEY_CLASS))
            else -> LinkedHashMap<String, Any?>().also { map ->
                json.keys().forEach { key -> map[key] = fromJson(json.get(key), resolve) }
            }
        }
        is JSONArray -> ArrayList<Any?>(json.length()).also { list ->
            for (i in 0 until json.length()) list.add(fromJson(json.get(i), resolve))
        }
        else -> json
    }

    fun argsFromJson(args: JSONArray?, resolve: (Long) -> Any): List<Any?> {
        if (args == null) return emptyList()
        return (0 until args.length()).map { fromJson(args.get(it), resolve) }
    }

    // -------------------------------------------------------------- expansion

    /**
     * A JSON snapshot instead of a handle: Bundles, Intents and plain Java objects are
     * flattened field by field. Requested with `{"$expand": true}` and used for events.
     */
    fun expand(value: Any?, sink: HandleSink?): Any = when (value) {
        null -> JSONObject.NULL
        is Intent -> intentToJson(value, sink)
        is Bundle -> bundleToJson(value, sink)
        is Collection<*> -> JSONArray().also { arr -> value.forEach { arr.put(expand(it, sink)) } }
        is Map<*, *> -> JSONObject().also { obj ->
            value.forEach { (k, v) -> obj.put(k.toString(), expand(v, sink)) }
        }
        else -> {
            val plain = toJson(value, null)
            // toJson only yields an opaque marker for things it can't represent; those
            // are the ones worth snapshotting field by field.
            if (plain is JSONObject && plain.has(KEY_OPAQUE)) pojoToJson(value, sink)
            else toJson(value, sink)
        }
    }

    fun intentToJson(intent: Intent, sink: HandleSink?): JSONObject {
        val out = JSONObject()
        out.put("action", intent.action ?: JSONObject.NULL)
        out.put("data", intent.dataString ?: JSONObject.NULL)
        out.put("type", intent.type ?: JSONObject.NULL)
        out.put("package", intent.`package` ?: JSONObject.NULL)
        out.put("component", intent.component?.flattenToShortString() ?: JSONObject.NULL)
        out.put("flags", intent.flags.toLong())
        out.put("categories", JSONArray().also { arr -> intent.categories?.forEach { arr.put(it) } })
        val extras = runCatching { intent.extras }.getOrNull()
        out.put("extras", if (extras == null) JSONObject() else bundleToJson(extras, sink))
        return out
    }

    @Suppress("DEPRECATION")
    fun bundleToJson(bundle: Bundle, sink: HandleSink?): JSONObject {
        val out = JSONObject()
        for (key in bundle.keySet()) {
            val value = runCatching { bundle.get(key) }.getOrNull()
            out.put(key, if (value is Bundle) bundleToJson(value, sink) else toJson(value, sink))
        }
        return out
    }

    /**
     * Public no-argument getters and public fields of a plain Java object.
     *
     * Accessibility is forced first: plenty of framework classes are package-private
     * or hidden, and reflection checks the *caller's* package, not the object's.
     */
    fun pojoToJson(value: Any, sink: HandleSink?): JSONObject {
        val out = JSONObject()
        out.put(KEY_POJO, value.javaClass.name)
        for (method in value.javaClass.methods) {
            if (method.parameterTypes.isNotEmpty()) continue
            if (method.declaringClass == Any::class.java) continue
            val name = method.name
            val property = when {
                name == "getClass" -> null
                name.startsWith("get") && name.length > 3 -> name.substring(3).decapitalize()
                name.startsWith("is") && name.length > 2 -> name.substring(2).decapitalize()
                else -> null
            } ?: continue
            if (!method.isAccessible) runCatching { method.isAccessible = true }
            val read = runCatching { method.invoke(value) }
            if (read.isSuccess) out.put(property, toJson(read.getOrNull(), sink))
        }
        for (field in value.javaClass.fields) {
            if (out.has(field.name)) continue
            if (!field.isAccessible) runCatching { field.isAccessible = true }
            val read = runCatching { field.get(value) }
            if (read.isSuccess) out.put(field.name, toJson(read.getOrNull(), sink))
        }
        return out
    }

    private fun String.decapitalize(): String =
        if (isEmpty()) this else this[0].lowercaseChar() + substring(1)
}
