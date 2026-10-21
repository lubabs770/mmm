package lubabs770.mmm.modemizer.rpc

import java.util.concurrent.atomic.AtomicLong

/**
 * Per-session table of Java objects the client holds references to.
 *
 * Handles are opaque longs. The client's garbage collector sends `release` in batches;
 * everything left is dropped when the session ends.
 *
 * Numbers come from one process-wide counter, never per table: a client that
 * reconnects may still hold (or garbage-collect) handles from its old session, and
 * those must fail as unknown rather than silently name a different object.
 */
class HandleTable(private val cap: Int = DEFAULT_CAP) {

    class CapExceeded(cap: Int) : RuntimeException("handle table full ($cap handles)")

    private val objects = HashMap<Long, Any>()

    val size: Int get() = synchronized(objects) { objects.size }

    /** Stores [value] and returns its handle. */
    fun put(value: Any): Long = synchronized(objects) {
        if (objects.size >= cap) throw CapExceeded(cap)
        val id = NEXT.getAndIncrement()
        objects[id] = value
        id
    }

    /** The object for [handle], or null if it was released or never existed. */
    fun get(handle: Long): Any? = synchronized(objects) { objects[handle] }

    /** Like [get] but throws a client-visible error instead of returning null. */
    fun require(handle: Long): Any =
        get(handle) ?: throw NoSuchElementException("unknown handle $handle")

    /** Returns true if the handle existed. */
    fun release(handle: Long): Boolean = synchronized(objects) { objects.remove(handle) != null }

    fun releaseAll(handles: Iterable<Long>): Int = handles.count { release(it) }

    fun clear() = synchronized(objects) { objects.clear() }

    companion object {
        const val DEFAULT_CAP = 50_000

        // Seeded from the clock, not 1: the service process is restarted by Android
        // all the time, and a client still holding handles from before that must not
        // find them naming objects of its new session.
        private val NEXT = AtomicLong(System.currentTimeMillis() * 1000)
    }
}
