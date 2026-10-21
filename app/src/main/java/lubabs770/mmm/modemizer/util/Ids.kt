package lubabs770.mmm.modemizer.util

import java.util.concurrent.atomic.AtomicLong

/**
 * Process-wide counters, so ids never repeat inside one run of the service.
 *
 * Event ids also have to stay unique *across* runs: durable backlogs are saved with
 * their ids and sorted and de-duplicated by them after a restart. So the event
 * counter starts from the clock (microsecond-ish, well inside 2^53), and
 * [eventsAbove] pushes it past anything restored from disk.
 */
object Ids {
    private val events = AtomicLong(System.currentTimeMillis() * 1000)
    private val sessions = AtomicLong(1)
    private val proxies = AtomicLong(1)

    fun nextEvent(): Long = events.getAndIncrement()
    fun nextSession(): Long = sessions.getAndIncrement()
    fun nextProxy(): Long = proxies.getAndIncrement()

    /** Makes sure the next event id is greater than [id]. */
    fun eventsAbove(id: Long) {
        events.updateAndGet { maxOf(it, id + 1) }
    }
}
