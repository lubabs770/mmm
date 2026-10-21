package lubabs770.mmm.modemizer.auth

import org.json.JSONArray
import org.json.JSONObject

/**
 * What a token is allowed to do. Enforced in the app, never in the client.
 *
 * A `full` token can reach all of Android — that is the point of MMM. A scoped token
 * carries an allow-list of method patterns (`class#method`, `*` matches any run of
 * characters) and of broadcast actions it may subscribe to.
 */
data class Scope(
    val full: Boolean,
    val methods: List<String> = emptyList(),
    val actions: List<String> = emptyList(),
) {

    fun allowsMethod(className: String, methodName: String): Boolean {
        if (full) return true
        val target = "$className#$methodName"
        return methods.any { matches(it, target) }
    }

    fun allowsAction(action: String): Boolean {
        if (full) return true
        return actions.any { matches(it, action) }
    }

    fun toJson(): JSONObject = JSONObject()
        .put("full", full)
        .put("methods", JSONArray(methods))
        .put("actions", JSONArray(actions))

    fun describe(): String = when {
        full -> "full"
        else -> "scoped: ${methods.size} method patterns, ${actions.size} actions"
    }

    companion object {
        val FULL = Scope(full = true)

        /** Default for a token paired for an AI assistant: SMS, contacts, status, notify. */
        fun mcpPreset(): Scope = Scope(
            full = false,
            methods = listOf(
                "$FACADE#*",
                "android.os.Build#*",
            ),
            actions = listOf(
                "android.provider.Telephony.SMS_RECEIVED",
                "android.intent.action.PHONE_STATE",
                "android.intent.action.BATTERY_CHANGED",
            ),
        )

        const val FACADE = "lubabs770.mmm.modemizer.rpc.Facade"

        fun fromJson(obj: JSONObject?): Scope {
            if (obj == null) return FULL
            return Scope(
                full = obj.optBoolean("full", false),
                methods = obj.optJSONArray("methods").toStringList(),
                actions = obj.optJSONArray("actions").toStringList(),
            )
        }

        private fun JSONArray?.toStringList(): List<String> {
            if (this == null) return emptyList()
            return (0 until length()).map { optString(it) }.filter { it.isNotEmpty() }
        }

        private val compiled = java.util.concurrent.ConcurrentHashMap<String, Regex>()

        /** Glob match where `*` stands for any run of characters. */
        fun matches(pattern: String, value: String): Boolean =
            compiled.getOrPut(pattern) { globToRegex(pattern) }.matches(value)

        private fun globToRegex(pattern: String): Regex {
            val regex = buildString {
                append('^')
                pattern.forEach { c ->
                    when (c) {
                        '*' -> append(".*")
                        else -> append(Regex.escape(c.toString()))
                    }
                }
                append('$')
            }
            return Regex(regex)
        }
    }
}
