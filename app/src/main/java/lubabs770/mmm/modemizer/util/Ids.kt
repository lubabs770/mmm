package lubabs770.mmm.modemizer.util

import java.util.concurrent.atomic.AtomicLong

/** Process-wide counters, so ids never repeat inside one run of the service. */
object Ids {
    private val events = AtomicLong(1)
    private val sessions = AtomicLong(1)
    private val proxies = AtomicLong(1)

    fun nextEvent(): Long = events.getAndIncrement()
    fun nextSession(): Long = sessions.getAndIncrement()
    fun nextProxy(): Long = proxies.getAndIncrement()
}
