package lubabs770.mmm.modemizer.settings

import android.content.Context

/**
 * RPC access to the Settings screen.
 *
 * Invariant from LLM.md: every setting the UI can change must also be settable over
 * RPC. Because the engine is generic reflection, that costs nothing more than these
 * two static methods:
 *
 *     a.cls("lubabs770.mmm.modemizer.settings.SettingsBridge").set(a.context, "port", 7400)
 *
 * A port change takes effect when the service restarts the listener.
 */
object SettingsBridge {

    @JvmStatic
    fun get(context: Context): Map<String, Any?> {
        val s = SettingsStore.get(context).current
        return linkedMapOf(
            "port" to s.port,
            "startOnBoot" to s.startOnBoot,
            "backlogEvents" to s.backlogEvents,
            "backlogHours" to s.backlogHours,
            "proxyReplyTimeoutMs" to s.proxyReplyTimeoutMs,
            "handleCap" to s.handleCap,
            "theme" to s.theme.name,
        )
    }

    /** Returns the settings as they are after the change. */
    @JvmStatic
    fun set(context: Context, key: String, value: Any?): Map<String, Any?> {
        SettingsStore.get(context).set(key, value)
        return get(context)
    }
}
