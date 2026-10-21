package lubabs770.mmm.modemizer.service

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.auth.TokenStore
import lubabs770.mmm.modemizer.events.EventHub
import lubabs770.mmm.modemizer.settings.SettingsStore

/**
 * The single place the UI and the service meet.
 *
 * The UI only reads state flows from here, so it never has to bind to the service;
 * the service owns the lifecycle (notification, boot, stop) and calls [start] and
 * [stop].
 */
object MmmRuntime {

    private val _running = MutableStateFlow(false)
    val running: StateFlow<Boolean> = _running.asStateFlow()

    @Volatile
    private var parts: Parts? = null

    private class Parts(
        val settings: SettingsStore,
        val tokens: TokenStore,
        val audit: AuditLog,
        val events: EventHub,
        val server: RpcServer,
    )

    private fun parts(context: Context): Parts = parts ?: synchronized(this) {
        parts ?: run {
            val app = context.applicationContext
            val settings = SettingsStore.get(app)
            val tokens = TokenStore.get(app)
            val audit = AuditLog.get(app)
            val events = EventHub(app, settings, audit)
            Parts(settings, tokens, audit, events, RpcServer(app, settings, tokens, audit, events))
                .also { parts = it }
        }
    }

    fun settings(context: Context): SettingsStore = parts(context).settings
    fun tokens(context: Context): TokenStore = parts(context).tokens
    fun audit(context: Context): AuditLog = parts(context).audit
    fun events(context: Context): EventHub = parts(context).events
    fun server(context: Context): RpcServer = parts(context).server

    /** Starts the listener and re-registers durable subscriptions. */
    fun start(context: Context) {
        val p = parts(context)
        if (!_running.value) p.events.restore()
        p.server.start(p.settings.current.port)
        _running.value = p.server.port.value != null
    }

    fun stop(context: Context) {
        val p = parts(context)
        p.server.stop()
        p.events.shutdown()
        _running.value = false
    }

    /** Re-binds the listener, for when the port setting changes. */
    fun restartListener(context: Context) {
        val p = parts(context)
        if (!_running.value) return
        p.server.stop()
        p.server.start(p.settings.current.port)
        _running.value = p.server.port.value != null
    }
}
