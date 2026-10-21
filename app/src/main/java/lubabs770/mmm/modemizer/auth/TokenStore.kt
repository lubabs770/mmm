package lubabs770.mmm.modemizer.auth

import android.content.Context
import java.io.File
import java.security.MessageDigest
import java.security.SecureRandom
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import lubabs770.mmm.modemizer.util.JsonFile
import org.json.JSONArray
import org.json.JSONObject

/** A paired client. The token itself is never stored, only its SHA-256. */
data class ClientToken(
    val id: String,
    val label: String,
    val hash: String,
    val scope: Scope,
    val createdAt: Long,
    val lastSeen: Long,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id)
        .put("label", label)
        .put("hash", hash)
        .put("scope", scope.toJson())
        .put("createdAt", createdAt)
        .put("lastSeen", lastSeen)

    companion object {
        fun fromJson(obj: JSONObject): ClientToken = ClientToken(
            id = obj.getString("id"),
            label = obj.optString("label", "client"),
            hash = obj.getString("hash"),
            scope = Scope.fromJson(obj.optJSONObject("scope")),
            createdAt = obj.optLong("createdAt"),
            lastSeen = obj.optLong("lastSeen"),
        )
    }
}

/**
 * The pairing table: create a token in the UI, authenticate with it over the socket.
 *
 * Tokens are 32 random bytes shown once as hex. Only the hash is kept, so a stolen
 * copy of the app's data can't be turned back into a working token.
 */
class TokenStore private constructor(private val file: File) {

    private val _clients = MutableStateFlow(load())
    val clients: StateFlow<List<ClientToken>> = _clients.asStateFlow()

    /** Returns the plaintext token; it is never recoverable afterwards. */
    fun create(label: String, scope: Scope): Pair<ClientToken, String> {
        val random = SecureRandom()
        val bytes = ByteArray(32)
        random.nextBytes(bytes)
        val token = bytes.joinToString("") { "%02x".format(it) }
        // The id is its own random value: it is stored in the clear and shown in the
        // UI, so it must reveal nothing about the token.
        val idBytes = ByteArray(4)
        random.nextBytes(idBytes)
        val now = System.currentTimeMillis()
        val client = ClientToken(
            id = idBytes.joinToString("") { "%02x".format(it) },
            label = label.ifBlank { "client" },
            hash = sha256(token),
            scope = scope,
            createdAt = now,
            lastSeen = 0L,
        )
        mutate { it + client }
        return client to token
    }

    fun verify(token: String): ClientToken? {
        val hash = sha256(token.trim())
        // Constant-time compare so a wrong token can't be narrowed down by timing.
        return _clients.value.firstOrNull { constantTimeEquals(it.hash, hash) }
    }

    fun touch(id: String) {
        val now = System.currentTimeMillis()
        mutate { list -> list.map { if (it.id == id) it.copy(lastSeen = now) else it } }
    }

    fun revoke(id: String) {
        mutate { list -> list.filterNot { it.id == id } }
    }

    /**
     * Every change is read-modify-write under one lock: without it, a `touch` racing
     * a `revoke` could write back the list it read before the revoke and bring the
     * revoked token back to life.
     */
    private fun mutate(change: (List<ClientToken>) -> List<ClientToken>) = synchronized(this) {
        _clients.value = change(_clients.value)
        save()
    }

    fun byId(id: String): ClientToken? = _clients.value.firstOrNull { it.id == id }

    private fun load(): List<ClientToken> {
        val obj = JsonFile.read(file) ?: return emptyList()
        val arr = obj.optJSONArray("tokens") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            runCatching { ClientToken.fromJson(arr.getJSONObject(i)) }.getOrNull()
        }
    }

    private fun save() {
        val arr = JSONArray()
        _clients.value.forEach { arr.put(it.toJson()) }
        JsonFile.write(file, JSONObject().put("tokens", arr))
    }

    companion object {
        @Volatile
        private var instance: TokenStore? = null

        fun get(context: Context): TokenStore = instance ?: synchronized(this) {
            instance ?: TokenStore(File(context.filesDir, "tokens.json")).also { instance = it }
        }

        fun sha256(text: String): String =
            MessageDigest.getInstance("SHA-256")
                .digest(text.toByteArray())
                .joinToString("") { "%02x".format(it) }

        fun constantTimeEquals(a: String, b: String): Boolean {
            if (a.length != b.length) return false
            var diff = 0
            for (i in a.indices) diff = diff or (a[i].code xor b[i].code)
            return diff == 0
        }
    }
}
