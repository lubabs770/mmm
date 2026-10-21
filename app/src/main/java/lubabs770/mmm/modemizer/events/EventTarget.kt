package lubabs770.mmm.modemizer.events

import lubabs770.mmm.modemizer.rpc.HandleSink

/**
 * A connected client, as far as the event machinery is concerned.
 *
 * Implemented by `Session`; kept as an interface so the event hub and the proxy
 * factory never touch sockets.
 */
interface EventTarget : HandleSink {
    val sessionId: Long

    /** False once the connection is gone; the hub then buffers or drops instead. */
    val alive: Boolean

    /** Delivers an event with an id the caller already allocated. */
    fun emit(sub: String, eventId: Long, ts: Long, payload: Any)

    /**
     * Delivers an event and blocks until the client sends `reply` for it, or
     * [timeoutMs] passes. Used for proxy methods that must return a value.
     */
    fun emitAwaitingReply(sub: String, payload: Any, timeoutMs: Long): Any?
}
