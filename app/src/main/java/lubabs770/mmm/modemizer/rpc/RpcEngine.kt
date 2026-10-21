package lubabs770.mmm.modemizer.rpc

import java.lang.reflect.Field
import java.lang.reflect.InvocationTargetException
import java.lang.reflect.Modifier

/**
 * The reflection half of the protocol: look up a class, construct, call, read, write.
 *
 * Targets are either a `Class` (a static call) or an instance. Nothing here knows
 * about sockets, sessions or JSON — [Session] marshals around it.
 */
object RpcEngine {

    /** Accepts `int`, `java.lang.String`, `java.lang.String[]`, `int[][]`. */
    fun resolveClass(name: String): Class<*> {
        val trimmed = name.trim()
        PRIMITIVES[trimmed]?.let { return it }
        if (trimmed.endsWith("[]")) {
            val component = resolveClass(trimmed.removeSuffix("[]"))
            return java.lang.reflect.Array.newInstance(component, 0).javaClass
        }
        return Class.forName(trimmed, false, RpcEngine::class.java.classLoader)
    }

    fun construct(className: String, args: List<Any?>, sig: List<String>?): Any {
        val cls = resolveClass(className)
        val ctor = Overloads.findConstructor(cls, args, sig)
        val coerced = Overloads.coerceArgs(ctor.parameterTypes, args)
        if (!ctor.isAccessible) runCatching { ctor.isAccessible = true }
        return unwrap { ctor.newInstance(*coerced) }!!
    }

    fun call(target: Any, methodName: String, args: List<Any?>, sig: List<String>?): Any? {
        val static = target is Class<*>
        val cls = if (static) target as Class<*> else target.javaClass
        val method = Overloads.findMethod(cls, methodName, args, sig, requireStatic = static)
        val coerced = Overloads.coerceArgs(method.parameterTypes, args)
        val receiver = if (Modifier.isStatic(method.modifiers)) null else target
        return unwrap { method.invoke(receiver, *coerced) }
    }

    fun getField(target: Any, fieldName: String): Any? {
        val static = target is Class<*>
        val cls = if (static) target as Class<*> else target.javaClass
        val field = findField(cls, fieldName)
        return unwrap { field.get(if (Modifier.isStatic(field.modifiers)) null else target) }
    }

    fun setField(target: Any, fieldName: String, value: Any?) {
        val static = target is Class<*>
        val cls = if (static) target as Class<*> else target.javaClass
        val field = findField(cls, fieldName)
        val coerced = Overloads.coerce(field.type, value)
        unwrap { field.set(if (Modifier.isStatic(field.modifiers)) null else target, coerced) }
    }

    /** The class name used for scope checks: the target class for statics. */
    fun targetClassName(target: Any): String =
        if (target is Class<*>) target.name else target.javaClass.name

    private fun findField(cls: Class<*>, name: String): Field {
        var next: Class<*>? = cls
        while (next != null) {
            val c: Class<*> = next
            val field = runCatching { c.getDeclaredField(name) }.getOrNull()
            if (field != null) {
                if (!field.isAccessible) runCatching { field.isAccessible = true }
                return field
            }
            next = c.superclass
        }
        // Interface constants live on the interfaces, not the superclass chain.
        for (i in cls.interfaces) {
            val field = runCatching { i.getDeclaredField(name) }.getOrNull()
            if (field != null) {
                if (!field.isAccessible) runCatching { field.isAccessible = true }
                return field
            }
        }
        throw NoSuchFieldException("no field '$name' on ${cls.name}")
    }

    /** Reflection wraps every failure; the client wants the real exception. */
    private fun <T> unwrap(block: () -> T): T = try {
        block()
    } catch (e: InvocationTargetException) {
        throw e.cause ?: e
    } catch (e: ExceptionInInitializerError) {
        throw e.cause ?: e
    }

    private val PRIMITIVES: Map<String, Class<*>> = mapOf(
        "void" to java.lang.Void.TYPE,
        "boolean" to java.lang.Boolean.TYPE,
        "byte" to java.lang.Byte.TYPE,
        "char" to java.lang.Character.TYPE,
        "short" to java.lang.Short.TYPE,
        "int" to java.lang.Integer.TYPE,
        "long" to java.lang.Long.TYPE,
        "float" to java.lang.Float.TYPE,
        "double" to java.lang.Double.TYPE,
    )
}
