package lubabs770.mmm.modemizer.service

import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface

/**
 * Finds this phone's Tailscale address and recognises Tailscale peers.
 *
 * Tailscale hands out IPv4 addresses from the CGNAT range 100.64.0.0/10. Plain
 * `java.net`, no Android types, so it can be unit-tested.
 */
object Tailnet {

    /** True for an address inside 100.64.0.0/10. */
    fun contains(address: InetAddress): Boolean {
        if (address !is Inet4Address) return false
        val b = address.address
        return (b[0].toInt() and 0xFF) == 100 && (b[1].toInt() and 0xC0) == 64
    }

    /**
     * The phone's own tailnet address, or null when Tailscale is not up.
     *
     * ponytail: IPv4 only; Tailscale's IPv6 range (fd7a:115c:a1e0::/48) can be added
     * if an IPv4-less tailnet ever shows up.
     */
    fun localAddress(): Inet4Address? = runCatching {
        NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
            .filter { it.isUp }
            .flatMap { it.inetAddresses.toList() }
            .filterIsInstance<Inet4Address>()
            .firstOrNull(::contains)
    }.getOrNull()
}
