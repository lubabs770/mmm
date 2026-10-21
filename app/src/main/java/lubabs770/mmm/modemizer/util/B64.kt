package lubabs770.mmm.modemizer.util

/**
 * Standard base64 with padding.
 *
 * Hand-rolled on purpose: `java.util.Base64` needs API 26 and `android.util.Base64`
 * can't be used from JVM unit tests, and this is 40 lines either way.
 */
object B64 {

    private const val ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

    private val REVERSE = IntArray(128) { -1 }.also { table ->
        ALPHABET.forEachIndexed { i, c -> table[c.code] = i }
    }

    fun encode(bytes: ByteArray): String {
        val out = StringBuilder((bytes.size + 2) / 3 * 4)
        var i = 0
        while (i + 2 < bytes.size) {
            val n = (bytes[i].toInt() and 0xFF shl 16) or
                (bytes[i + 1].toInt() and 0xFF shl 8) or
                (bytes[i + 2].toInt() and 0xFF)
            out.append(ALPHABET[n ushr 18 and 0x3F])
            out.append(ALPHABET[n ushr 12 and 0x3F])
            out.append(ALPHABET[n ushr 6 and 0x3F])
            out.append(ALPHABET[n and 0x3F])
            i += 3
        }
        when (bytes.size - i) {
            1 -> {
                val n = bytes[i].toInt() and 0xFF shl 16
                out.append(ALPHABET[n ushr 18 and 0x3F])
                out.append(ALPHABET[n ushr 12 and 0x3F])
                out.append("==")
            }
            2 -> {
                val n = (bytes[i].toInt() and 0xFF shl 16) or (bytes[i + 1].toInt() and 0xFF shl 8)
                out.append(ALPHABET[n ushr 18 and 0x3F])
                out.append(ALPHABET[n ushr 12 and 0x3F])
                out.append(ALPHABET[n ushr 6 and 0x3F])
                out.append('=')
            }
        }
        return out.toString()
    }

    fun decode(text: String): ByteArray {
        val clean = text.filter { it.code < 128 && REVERSE[it.code] >= 0 }
        val out = ByteArray(clean.length * 3 / 4)
        var acc = 0
        var bits = 0
        var pos = 0
        for (c in clean) {
            acc = (acc shl 6) or REVERSE[c.code]
            bits += 6
            if (bits >= 8) {
                bits -= 8
                out[pos++] = (acc ushr bits and 0xFF).toByte()
            }
        }
        return if (pos == out.size) out else out.copyOf(pos)
    }
}
