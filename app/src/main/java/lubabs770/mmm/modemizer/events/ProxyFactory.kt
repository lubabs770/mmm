package lubabs770.mmm.modemizer.events

import java.lang.reflect.InvocationHandler
import java.lang.reflect.Method
import java.lang.reflect.Proxy
import lubabs770.mmm.modemizer.rpc.Marshal
import lubabs770.mmm.modemizer.rpc.Overloads
import lubabs770.mmm.modemizer.util.Ids
import org.json.JSONArray
import org.json.JSONObject

/**
 * Turns a Python function into a Java listener.
 *
 * `proxy {interfaces}` returns a handle to an object implementing those interfaces;
 * every call on it is sent to the client as an event. A `void` method returns
 * immediately, and anything else waits for the client's `reply` — or for the timeout,
 * after which a harmless default is returned, because an Android callback that never
 * returns can take the system service down with it.
 *
 * Proxy events are live-only: they carry handles belonging to this session and a
 * reply has nowhere to go once the client is gone, so they are never buffered.
 */
object ProxyFactory {

    /** The proxy object plus the subscription name its events arrive under. */
    data class Created(val proxy: Any, val subscription: String)

    fun create(
        target: EventTarget,
        interfaceNames: List<String>,
        timeoutMs: Long,
    ): Created {
        require(interfaceNames.isNotEmpty()) { "proxy needs at least one interface" }
        val interfaces = interfaceNames.map { name ->
            val cls = Class.forName(name)
            require(cls.isInterface) { "$name is not an interface" }
            cls
        }.toTypedArray()
        val subscription = "proxy#${Ids.nextProxy()}"
        val handler = Handler(target, subscription, timeoutMs)
        val proxy = Proxy.newProxyInstance(ProxyFactory::class.java.classLoader, interfaces, handler)
        return Created(proxy, subscription)
    }

    private class Handler(
        private val target: EventTarget,
        private val subscription: String,
        private val timeoutMs: Long,
    ) : InvocationHandler {

        override fun invoke(proxy: Any, method: Method, args: Array<out Any?>?): Any? {
            // Object's own methods must not become round trips.
            when (method.name) {
                "hashCode" -> if (method.parameterTypes.isEmpty()) return System.identityHashCode(proxy)
                "equals" -> if (method.parameterTypes.size == 1) return proxy === args?.get(0)
                "toString" -> if (method.parameterTypes.isEmpty()) return "MmmProxy($subscription)"
            }

            val payload = JSONObject()
                .put("proxy", subscription)
                .put("method", method.name)
                .put("sig", JSONArray(Overloads.typeNames(method.parameterTypes)))
                .put(
                    "args",
                    JSONArray().also { arr ->
                        args?.forEach { arr.put(Marshal.expand(it, target)) }
                    }
                )

            val returns = method.returnType
            if (returns == Void.TYPE) {
                target.emit(subscription, Ids.nextEvent(), System.currentTimeMillis(), payload)
                return null
            }
            val reply = runCatching { target.emitAwaitingReply(subscription, payload, timeoutMs) }
                .getOrNull()
            return Overloads.coerce(returns, reply) ?: defaultValue(returns)
        }

        private fun defaultValue(type: Class<*>): Any? = when (type) {
            java.lang.Boolean.TYPE -> false
            java.lang.Byte.TYPE -> 0.toByte()
            java.lang.Character.TYPE -> '\u0000'
            java.lang.Short.TYPE -> 0.toShort()
            java.lang.Integer.TYPE -> 0
            java.lang.Long.TYPE -> 0L
            java.lang.Float.TYPE -> 0f
            java.lang.Double.TYPE -> 0.0
            else -> null
        }
    }
}
