package lubabs770.mmm.modemizer

import lubabs770.mmm.modemizer.util.Ids
import org.junit.Assert.assertTrue
import org.junit.Test

class IdsTest {

    @Test
    fun `event ids start from the clock, so a restart does not reuse saved ones`() {
        assertTrue(Ids.nextEvent() >= 1_700_000_000_000L * 1000)
    }

    @Test
    fun `restored ids push the counter past them`() {
        val far = Ids.nextEvent() + 1_000_000
        Ids.eventsAbove(far)
        assertTrue(Ids.nextEvent() > far)
        // Never backwards.
        Ids.eventsAbove(1)
        assertTrue(Ids.nextEvent() > far)
    }
}
