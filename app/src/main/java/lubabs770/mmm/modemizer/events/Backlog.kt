package lubabs770.mmm.modemizer.events

/**
 * Ring buffer of events for a durable subscription.
 *
 * A durable subscription keeps receiving while nobody is connected; its events wait
 * here until the client resubscribes, at which point they are drained in order. The
 * buffer is bounded both ways: oldest events are dropped once [maxEvents] is reached,
 * and events older than [maxAgeMs] are dropped whenever the buffer is touched.
 *
 * Deliberately free of Android types so it can be unit-tested on the JVM.
 */
class Backlog(
    val maxEvents: Int = DEFAULT_MAX_EVENTS,
    val maxAgeMs: Long = DEFAULT_MAX_AGE_MS,
    private val clock: () -> Long = System::currentTimeMillis,
) {

    data class Entry(val eventId: Long, val ts: Long, val json: String)

    private val entries = ArrayDeque<Entry>()

    /** Events dropped because the buffer was full or they aged out. */
    var dropped: Long = 0
        private set

    val size: Int get() = synchronized(entries) { prune(); entries.size }

    fun add(entry: Entry) = synchronized(entries) {
        prune()
        entries.addLast(entry)
        while (entries.size > maxEvents) {
            entries.removeFirst()
            dropped++
        }
    }

    /** Oldest first, without removing anything. */
    fun snapshot(): List<Entry> = synchronized(entries) {
        prune()
        entries.toList()
    }

    /** Returns everything waiting and empties the buffer. */
    fun drain(): List<Entry> = synchronized(entries) {
        prune()
        val out = entries.toList()
        entries.clear()
        out
    }

    /**
     * Puts back events that were drained but never reached the client, ahead of
     * anything that arrived since. Still bounded by [maxEvents].
     */
    fun requeue(undelivered: List<Entry>) = synchronized(entries) {
        val merged = (undelivered + entries).distinctBy { it.eventId }.sortedBy { it.eventId }
        entries.clear()
        merged.forEach { entries.addLast(it) }
        while (entries.size > maxEvents) {
            entries.removeFirst()
            dropped++
        }
        prune()
    }

    fun restore(saved: List<Entry>) = synchronized(entries) {
        entries.clear()
        saved.sortedBy { it.eventId }.takeLast(maxEvents).forEach { entries.addLast(it) }
        prune()
    }

    fun clear() = synchronized(entries) {
        entries.clear()
        dropped = 0
    }

    private fun prune() {
        if (maxAgeMs <= 0) return
        val cutoff = clock() - maxAgeMs
        while (entries.isNotEmpty() && entries.first().ts < cutoff) {
            entries.removeFirst()
            dropped++
        }
    }

    companion object {
        const val DEFAULT_MAX_EVENTS = 500
        const val DEFAULT_MAX_AGE_MS = 24L * 60 * 60 * 1000
    }
}
