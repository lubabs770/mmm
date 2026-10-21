package lubabs770.mmm.modemizer.rpc

import java.lang.reflect.Constructor
import java.lang.reflect.Method
import java.lang.reflect.Modifier

/**
 * Java overload resolution for arguments that arrived as JSON.
 *
 * JSON only carries a few types, so a number could mean `int`, `long`, `float`… The
 * engine therefore collects every method whose parameters *could* accept the given
 * arguments and picks the most specific one. When two candidates are equally specific
 * it refuses and lists them, so the client can force a choice with `sig`.
 *
 * Deliberately free of Android types so it can be unit-tested on the JVM.
 */
object Overloads {

    class NoMatch(message: String) : RuntimeException(message)

    class Ambiguous(val candidates: List<String>) :
        RuntimeException("ambiguous overload; pass sig to choose: ${candidates.joinToString("; ")}")

    // ---------------------------------------------------------------- lookup

    fun findMethod(
        cls: Class<*>,
        name: String,
        args: List<Any?>,
        sig: List<String>? = null,
        requireStatic: Boolean = false,
    ): Method {
        val named = allMethods(cls)
            .filter { it.name == name && !it.isBridge && !it.isSynthetic }
            .filter { !requireStatic || Modifier.isStatic(it.modifiers) }
        if (named.isEmpty()) {
            val what = if (requireStatic) "static method" else "method"
            throw NoMatch("no $what '$name' on ${cls.name}")
        }
        if (sig != null) {
            return named.firstOrNull { typeNames(it.parameterTypes) == sig }
                ?: throw NoMatch(
                    "no method '$name' on ${cls.name} with sig [${sig.joinToString(", ")}]; " +
                        "have ${named.joinToString("; ") { describe(it) }}"
                )
        }
        val fitting = named
            .filter { it.parameterTypes.size == args.size && fitsAll(it.parameterTypes, args) }
        if (fitting.isEmpty()) {
            throw NoMatch(
                "no overload of ${cls.name}.$name accepts (${argTypes(args)}); " +
                    "have ${named.joinToString("; ") { describe(it) }}"
            )
        }
        return pick(fitting, args, { it.parameterTypes }, { describe(it) })
    }

    fun findConstructor(cls: Class<*>, args: List<Any?>, sig: List<String>? = null): Constructor<*> {
        val all = cls.declaredConstructors.filter { !it.isSynthetic }
        if (sig != null) {
            return all.firstOrNull { typeNames(it.parameterTypes) == sig }
                ?: throw NoMatch(
                    "no constructor of ${cls.name} with sig [${sig.joinToString(", ")}]; " +
                        "have ${all.joinToString("; ") { describe(it) }}"
                )
        }
        val fitting = all.filter { it.parameterTypes.size == args.size && fitsAll(it.parameterTypes, args) }
        if (fitting.isEmpty()) {
            throw NoMatch(
                "no constructor of ${cls.name} accepts (${argTypes(args)}); " +
                    "have ${all.joinToString("; ") { describe(it) }}"
            )
        }
        return pick(fitting, args, { it.parameterTypes }, { describe(it) })
    }

    /** Every method reachable on [cls], including inherited and package-private ones. */
    private fun allMethods(cls: Class<*>): List<Method> {
        val out = LinkedHashMap<String, Method>()
        fun add(m: Method) {
            val key = m.name + "(" + typeNames(m.parameterTypes).joinToString(",") + ")"
            // Keep the most derived declaration of the same signature.
            val existing = out[key]
            if (existing == null || existing.declaringClass.isAssignableFrom(m.declaringClass)) {
                out[key] = m
            }
        }
        cls.methods.forEach(::add)
        var next: Class<*>? = cls
        while (next != null) {
            val c: Class<*> = next
            runCatching { c.declaredMethods.forEach(::add) }
            c.interfaces.forEach { i -> runCatching { i.methods.forEach(::add) } }
            next = c.superclass
        }
        return out.values.toList().onEach { m ->
            if (!m.isAccessible) runCatching { m.isAccessible = true }
        }
    }

    private fun <T> pick(
        candidates: List<T>,
        args: List<Any?>,
        params: (T) -> Array<Class<*>>,
        describe: (T) -> String,
    ): T {
        if (candidates.size == 1) return candidates[0]
        // JSON carries no type width, so an Integer argument "fits" both int and long.
        // Score each parameter against the argument that actually arrived and keep the
        // closest matches; only then fall back to Java-style specificity.
        val scored = candidates.map { it to distance(params(it), args) }
        val closest = scored.minOf { it.second }
        val shortlist = scored.filter { it.second == closest }.map { it.first }
        if (shortlist.size == 1) return shortlist[0]
        // Keep only the maximal elements of "no other candidate is more specific".
        val best = shortlist.filter { a ->
            shortlist.none { b -> b !== a && moreSpecific(params(b), params(a)) }
        }
        if (best.size == 1) return best[0]
        val distinct = best.distinctBy { typeNames(params(it)) }
        if (distinct.size == 1) return distinct[0]
        throw Ambiguous(distinct.map(describe))
    }

    private fun distance(params: Array<Class<*>>, args: List<Any?>): Int =
        params.indices.sumOf { distance(params[it], args[it]) }

    /** 0 is an exact match, bigger means more conversion. */
    private fun distance(param: Class<*>, arg: Any?): Int {
        if (arg == null) return if (param == java.lang.Object::class.java) 3 else 1
        val p = box(param)
        val a: Class<*> = arg.javaClass
        if (p == a) return 0
        if (p == java.lang.Object::class.java) return 3
        if (arg is Number && box(p) in BOXES.values && p != java.lang.Boolean::class.java) return 1
        if (p.isAssignableFrom(a)) return 1
        return 2
    }

    /** True when every parameter of [a] is assignable to the matching one of [b]. */
    private fun moreSpecific(a: Array<Class<*>>, b: Array<Class<*>>): Boolean {
        if (a.size != b.size) return false
        var strictly = false
        for (i in a.indices) {
            val x = box(a[i])
            val y = box(b[i])
            if (x == y) continue
            if (!y.isAssignableFrom(x)) return false
            strictly = true
        }
        return strictly
    }

    // ------------------------------------------------------------- arg fitting

    private fun fitsAll(params: Array<Class<*>>, args: List<Any?>): Boolean =
        params.indices.all { fits(params[it], args[it]) }

    /** Could [arg], as decoded from JSON, be passed as a [param]? */
    fun fits(param: Class<*>, arg: Any?): Boolean {
        if (arg == null) return !param.isPrimitive
        if (param.isPrimitive || isBoxed(param)) {
            val p = box(param)
            return when {
                p == java.lang.Boolean::class.java -> arg is Boolean
                p == java.lang.Character::class.java -> arg is Char || (arg is String && arg.length == 1)
                else -> arg is Number || (arg is Char && p != java.lang.Boolean::class.java)
            }
        }
        if (param == java.lang.String::class.java || param == CharSequence::class.java) {
            return arg is CharSequence
        }
        if (param.isArray) {
            if (arg is ByteArray && param.componentType == java.lang.Byte.TYPE) return true
            return arg is List<*> || (arg.javaClass.isArray)
        }
        if (param == java.lang.Object::class.java) return true
        return param.isInstance(arg)
    }

    // ------------------------------------------------------------- coercion

    /** Converts JSON-decoded [args] into the exact types [params] wants. */
    fun coerceArgs(params: Array<Class<*>>, args: List<Any?>): Array<Any?> =
        Array(params.size) { coerce(params[it], args[it]) }

    fun coerce(param: Class<*>, arg: Any?): Any? {
        if (arg == null) return null
        val p = box(param)
        if (p == java.lang.Boolean::class.java) {
            return arg as? Boolean ?: ((arg as? Number)?.toInt() ?: 0) != 0
        }
        if (p == java.lang.Character::class.java) {
            return when (arg) {
                is Char -> arg
                is String -> arg[0]
                is Number -> arg.toInt().toChar()
                else -> arg
            }
        }
        if (arg is Number || arg is Char) {
            val n: Number = if (arg is Char) arg.code else arg as Number
            when (p) {
                java.lang.Integer::class.java -> return n.toInt()
                java.lang.Long::class.java -> return n.toLong()
                java.lang.Short::class.java -> return n.toShort()
                java.lang.Byte::class.java -> return n.toByte()
                java.lang.Float::class.java -> return n.toFloat()
                java.lang.Double::class.java -> return n.toDouble()
            }
        }
        if (param == java.lang.String::class.java && arg is CharSequence) return arg.toString()
        if (param.isArray) {
            val component = param.componentType!!
            if (arg is ByteArray && component == java.lang.Byte.TYPE) return arg
            val items: List<Any?> = when {
                arg is List<*> -> arg
                arg.javaClass.isArray -> (0 until java.lang.reflect.Array.getLength(arg))
                    .map { java.lang.reflect.Array.get(arg, it) }
                else -> return arg
            }
            val out = java.lang.reflect.Array.newInstance(component, items.size)
            items.forEachIndexed { i, item ->
                java.lang.reflect.Array.set(out, i, coerce(component, item))
            }
            return out
        }
        return arg
    }

    // ------------------------------------------------------------- helpers

    fun typeNames(types: Array<Class<*>>): List<String> = types.map { it.canonicalName ?: it.name }

    fun describe(m: Method): String =
        (if (Modifier.isStatic(m.modifiers)) "static " else "") +
            "${m.name}(${typeNames(m.parameterTypes).joinToString(", ")})"

    fun describe(c: Constructor<*>): String =
        "${c.declaringClass.simpleName}(${typeNames(c.parameterTypes).joinToString(", ")})"

    private fun argTypes(args: List<Any?>): String =
        args.joinToString(", ") { it?.javaClass?.simpleName ?: "null" }

    private fun isBoxed(c: Class<*>): Boolean = c in BOXES.values

    private fun box(c: Class<*>): Class<*> = if (c.isPrimitive) BOXES[c] ?: c else c

    private val BOXES: Map<Class<*>, Class<*>> = mapOf(
        java.lang.Boolean.TYPE to java.lang.Boolean::class.java,
        java.lang.Byte.TYPE to java.lang.Byte::class.java,
        java.lang.Character.TYPE to java.lang.Character::class.java,
        java.lang.Short.TYPE to java.lang.Short::class.java,
        java.lang.Integer.TYPE to java.lang.Integer::class.java,
        java.lang.Long.TYPE to java.lang.Long::class.java,
        java.lang.Float.TYPE to java.lang.Float::class.java,
        java.lang.Double.TYPE to java.lang.Double::class.java,
    )
}
