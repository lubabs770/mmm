package lubabs770.mmm.modemizer

import lubabs770.mmm.modemizer.events.Backlog
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BacklogTest {

    private var now = 1_000_000L
    private fun backlog(maxEvents: Int = 3, maxAgeMs: Long = 10_000) =
        Backlog(maxEvents, maxAgeMs) { now }

    private fun entry(id: Long) = Backlog.Entry(id, now, """{"n":$id}""")

    @Test
    fun `events come back oldest first`() {
        val log = backlog()
        (1L..3L).forEach { log.add(entry(it)) }
        assertEquals(listOf(1L, 2L, 3L), log.drain().map { it.eventId })
    }

    @Test
    fun `draining empties the buffer`() {
        val log = backlog()
        log.add(entry(1))
        assertEquals(1, log.drain().size)
        assertEquals(0, log.size)
        assertTrue(log.drain().isEmpty())
    }

    @Test
    fun `the oldest events are dropped once it is full`() {
        val log = backlog(maxEvents = 3)
        (1L..5L).forEach { log.add(entry(it)) }
        assertEquals(listOf(3L, 4L, 5L), log.drain().map { it.eventId })
        assertEquals(2, log.dropped)
    }

    @Test
    fun `events older than the age limit are dropped`() {
        val log = backlog(maxEvents = 100, maxAgeMs = 10_000)
        log.add(entry(1))
        now += 9_000
        log.add(entry(2))
        now += 2_000 // entry 1 is now 11s old
        assertEquals(listOf(2L), log.drain().map { it.eventId })
        assertEquals(1, log.dropped)
    }

    @Test
    fun `age zero means never expire`() {
        val log = backlog(maxEvents = 10, maxAgeMs = 0)
        log.add(entry(1))
        now += 10_000_000
        assertEquals(1, log.size)
    }

    @Test
    fun `restore keeps the newest events in order`() {
        val log = backlog(maxEvents = 2)
        log.restore(listOf(entry(3), entry(1), entry(2)))
        assertEquals(listOf(2L, 3L), log.drain().map { it.eventId })
    }

    @Test
    fun `snapshot leaves the buffer alone`() {
        val log = backlog()
        log.add(entry(1))
        assertEquals(1, log.snapshot().size)
        assertEquals(1, log.size)
    }

    @Test
    fun `clear resets the drop counter too`() {
        val log = backlog(maxEvents = 1)
        log.add(entry(1))
        log.add(entry(2))
        assertEquals(1, log.dropped)
        log.clear()
        assertEquals(0, log.size)
        assertEquals(0, log.dropped)
    }

    @Test
    fun `requeued events go back ahead of newer ones`() {
        val log = backlog(maxEvents = 5)
        (1L..3L).forEach { log.add(entry(it)) }
        val drained = log.drain()
        log.add(entry(4))
        log.requeue(drained.drop(1))
        assertEquals(listOf(2L, 3L, 4L), log.snapshot().map { it.eventId })
    }

    @Test
    fun `requeue still respects the cap`() {
        val log = backlog(maxEvents = 2)
        log.add(entry(3))
        log.requeue(listOf(entry(1), entry(2)))
        assertEquals(listOf(2L, 3L), log.snapshot().map { it.eventId })
        assertEquals(1L, log.dropped)
    }
}
