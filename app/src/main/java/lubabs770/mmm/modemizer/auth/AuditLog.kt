package lubabs770.mmm.modemizer.auth

import android.content.Context
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import lubabs770.mmm.modemizer.util.JsonFile
import org.json.JSONArray
import org.json.JSONObject

/**
 * Rolling record of what clients did, kept on the device and shown in the Activity
 * screen.
 *
 * It records that something happened, not the arguments: connects, authentication
 * failures, subscriptions, rejected calls, and a per-client count of method calls.
 */
class AuditLog private constructor(private val file: File) {

    enum class Kind { SERVICE, CONNECT, AUTH_OK, AUTH_FAIL, SUBSCRIBE, REJECT, PAIR, REVOKE, ERROR }

    data class Entry(
        val ts: Long,
        val kind: Kind,
        val client: String?,
        val detail: String,
    ) {
        fun toJson(): JSONObject = JSONObject()
            .put("ts", ts)
            .put("kind", kind.name)
            .put("client", client ?: JSONObject.NULL)
            .put("detail", detail)

        companion object {
            fun fromJson(obj: JSONObject) = Entry(
                ts = obj.optLong("ts"),
                kind = runCatching { Kind.valueOf(obj.getString("kind")) }.getOrDefault(Kind.SERVICE),
                client = obj.optString("client").takeIf { it.isNotEmpty() },
                detail = obj.optString("detail"),
            )
        }
    }

    private val _entries = MutableStateFlow(loadEntries())
    val entries: StateFlow<List<Entry>> = _entries.asStateFlow()

    private val _callCounts = MutableStateFlow(loadCounts())
    val callCounts: StateFlow<Map<String, Long>> = _callCounts.asStateFlow()

    private var dirtySince = 0L

    fun log(kind: Kind, detail: String, client: String? = null) {
        val entry = Entry(System.currentTimeMillis(), kind, client, detail)
        synchronized(this) {
            _entries.value = (_entries.value + entry).takeLast(MAX_ENTRIES)
            save()
        }
    }

    /** Counts a method call without recording its arguments. */
    fun countCall(clientId: String) = synchronized(this) {
        val counts = _callCounts.value.toMutableMap()
        counts[clientId] = (counts[clientId] ?: 0L) + 1
        _callCounts.value = counts
        // Counters are chatty; flush at most every few seconds.
        val now = System.currentTimeMillis()
        if (now - dirtySince > FLUSH_MS) {
            dirtySince = now
            save()
        }
    }

    /** Writes any call counts still waiting for the next throttled flush. */
    fun flush() = synchronized(this) { save() }

    fun clear() = synchronized(this) {
        _entries.value = emptyList()
        _callCounts.value = emptyMap()
        save()
    }

    private fun save() {
        val arr = JSONArray()
        _entries.value.forEach { arr.put(it.toJson()) }
        val counts = JSONObject()
        _callCounts.value.forEach { (id, n) -> counts.put(id, n) }
        JsonFile.write(file, JSONObject().put("entries", arr).put("calls", counts))
    }

    private fun loadEntries(): List<Entry> {
        val arr = JsonFile.read(file)?.optJSONArray("entries") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            runCatching { Entry.fromJson(arr.getJSONObject(i)) }.getOrNull()
        }
    }

    private fun loadCounts(): Map<String, Long> {
        val obj = JsonFile.read(file)?.optJSONObject("calls") ?: return emptyMap()
        return obj.keys().asSequence().associateWith { obj.optLong(it) }
    }

    companion object {
        const val MAX_ENTRIES = 500
        private const val FLUSH_MS = 5_000L

        @Volatile
        private var instance: AuditLog? = null

        fun get(context: Context): AuditLog = instance ?: synchronized(this) {
            instance ?: AuditLog(File(context.filesDir, "audit.json")).also { instance = it }
        }
    }
}
