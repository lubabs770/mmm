package lubabs770.mmm.modemizer.settings

import android.content.Context
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import lubabs770.mmm.modemizer.util.JsonFile
import org.json.JSONObject

enum class ThemeMode { SYSTEM, LIGHT, DARK }

/**
 * Everything the Settings screen can change.
 *
 * Invariant: every field here is also readable and writable over RPC through
 * [SettingsBridge], so a client never needs the UI.
 */
data class MmmSettings(
    val port: Int = DEFAULT_PORT,
    val startOnBoot: Boolean = true,
    val backlogEvents: Int = 500,
    val backlogHours: Int = 24,
    val proxyReplyTimeoutMs: Long = 2_000,
    val handleCap: Int = 50_000,
    val theme: ThemeMode = ThemeMode.SYSTEM,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("port", port)
        .put("startOnBoot", startOnBoot)
        .put("backlogEvents", backlogEvents)
        .put("backlogHours", backlogHours)
        .put("proxyReplyTimeoutMs", proxyReplyTimeoutMs)
        .put("handleCap", handleCap)
        .put("theme", theme.name)

    companion object {
        const val DEFAULT_PORT = 7357

        fun fromJson(obj: JSONObject?): MmmSettings {
            val d = MmmSettings()
            if (obj == null) return d
            return MmmSettings(
                port = obj.optInt("port", d.port).coerceIn(1024, 65535),
                startOnBoot = obj.optBoolean("startOnBoot", d.startOnBoot),
                backlogEvents = obj.optInt("backlogEvents", d.backlogEvents).coerceIn(0, 100_000),
                backlogHours = obj.optInt("backlogHours", d.backlogHours).coerceIn(0, 24 * 30),
                proxyReplyTimeoutMs = obj.optLong("proxyReplyTimeoutMs", d.proxyReplyTimeoutMs)
                    .coerceIn(100, 60_000),
                handleCap = obj.optInt("handleCap", d.handleCap).coerceIn(100, 1_000_000),
                theme = runCatching { ThemeMode.valueOf(obj.optString("theme")) }
                    .getOrDefault(d.theme),
            )
        }
    }
}

class SettingsStore private constructor(private val file: File) {

    private val _state = MutableStateFlow(MmmSettings.fromJson(JsonFile.read(file)))
    val state: StateFlow<MmmSettings> = _state.asStateFlow()

    val current: MmmSettings get() = _state.value

    fun update(block: (MmmSettings) -> MmmSettings) {
        _state.value = block(_state.value)
        JsonFile.write(file, _state.value.toJson())
    }

    /** Applies one named field; shared by the UI and the RPC bridge. */
    fun set(key: String, value: Any?): MmmSettings {
        update { s ->
            when (key) {
                "port" -> s.copy(port = asInt(value, s.port).coerceIn(1024, 65535))
                "startOnBoot" -> s.copy(startOnBoot = asBool(value, s.startOnBoot))
                "backlogEvents" -> s.copy(backlogEvents = asInt(value, s.backlogEvents).coerceIn(0, 100_000))
                "backlogHours" -> s.copy(backlogHours = asInt(value, s.backlogHours).coerceIn(0, 24 * 30))
                "proxyReplyTimeoutMs" ->
                    s.copy(proxyReplyTimeoutMs = asInt(value, s.proxyReplyTimeoutMs.toInt()).toLong().coerceIn(100, 60_000))
                "handleCap" -> s.copy(handleCap = asInt(value, s.handleCap).coerceIn(100, 1_000_000))
                "theme" -> s.copy(
                    theme = runCatching { ThemeMode.valueOf(value.toString().uppercase()) }
                        .getOrElse { throw IllegalArgumentException("theme must be SYSTEM, LIGHT or DARK") }
                )
                else -> throw IllegalArgumentException("unknown setting '$key'")
            }
        }
        return current
    }

    private fun asInt(value: Any?, fallback: Int): Int = when (value) {
        is Number -> value.toInt()
        is String -> value.toIntOrNull() ?: fallback
        else -> fallback
    }

    private fun asBool(value: Any?, fallback: Boolean): Boolean = when (value) {
        is Boolean -> value
        is Number -> value.toInt() != 0
        is String -> value.equals("true", ignoreCase = true)
        else -> fallback
    }

    companion object {
        @Volatile
        private var instance: SettingsStore? = null

        fun get(context: Context): SettingsStore = instance ?: synchronized(this) {
            instance ?: SettingsStore(File(context.filesDir, "settings.json")).also { instance = it }
        }
    }
}
