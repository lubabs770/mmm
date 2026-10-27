package lubabs770.mmm.modemizer

import java.net.InetAddress
import lubabs770.mmm.modemizer.service.Tailnet
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TailnetTest {

    private fun ip(text: String) = InetAddress.getByName(text)

    @Test
    fun `the whole of 100_64_0_0 slash 10 is tailnet`() {
        assertTrue(Tailnet.contains(ip("100.64.0.0")))
        assertTrue(Tailnet.contains(ip("100.101.102.103")))
        assertTrue(Tailnet.contains(ip("100.127.255.255")))
    }

    @Test
    fun `the neighbours of the range are not`() {
        assertFalse(Tailnet.contains(ip("100.63.255.255")))
        assertFalse(Tailnet.contains(ip("100.128.0.0")))
        assertFalse(Tailnet.contains(ip("192.168.1.10")))
        assertFalse(Tailnet.contains(ip("127.0.0.1")))
        assertFalse(Tailnet.contains(ip("::1")))
    }
}
