package lubabs770.mmm.modemizer.service

import android.content.Context
import android.os.Build
import android.util.Log
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import lubabs770.mmm.modemizer.BuildConfig
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.auth.TokenStore
import lubabs770.mmm.modemizer.events.EventHub
import lubabs770.mmm.modemizer.rpc.Session
import lubabs770.mmm.modemizer.settings.SettingsStore
import org.json.JSONObject

/**
 * The listener on 127.0.0.1.
 *
 * Loopback only, never a network interface: a remote computer reaches it through an
 * SSH tunnel or `adb forward`, both of which bring their own authentication. Every
 * connection still has to present a token.
 */
class RpcServer(
    private val appContext: Context,
    private val settings: SettingsStore,
    private val tokens: TokenStore,
    private val audit: AuditLog,
    private val events: EventHub,
) {

    data class ClientInfo(
        val sessionId: Long,
        val label: String,
        val clientId: String?,
        val since: Long,
        val handles: Int,
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val sessions = CopyOnWriteArrayList<Session>()

    private var serverSocket: ServerSocket? = null
    private var acceptJob: Job? = null

    private val _port = MutableStateFlow<Int?>(null)
    /** The port being listened on, or null when the server is down. */
    val port: StateFlow<Int?> = _port.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _clients = MutableStateFlow<List<ClientInfo>>(emptyList())
    val clients: StateFlow<List<ClientInfo>> = _clients.asStateFlow()

    fun start(port: Int) {
        if (_port.value == port && acceptJob?.isActive == true) return
        stop()
        val socket = try {
            ServerSocket(port, BACKLOG, InetAddress.getByName(LOOPBACK))
        } catch (e: Exception) {
            _error.value = "cannot listen on $LOOPBACK:$port — ${e.message}"
            audit.log(AuditLog.Kind.ERROR, "listen on $port failed: ${e.message}")
            return
        }
        serverSocket = socket
        _port.value = port
        _error.value = null
        audit.log(AuditLog.Kind.SERVICE, "listening on $LOOPBACK:$port")
        acceptJob = scope.launch { acceptLoop(socket) }
    }

    private suspend fun acceptLoop(socket: ServerSocket) {
        while (!socket.isClosed) {
            val connection: Socket = try {
                socket.accept()
            } catch (e: Exception) {
                if (!socket.isClosed) Log.w(TAG, "accept failed", e)
                break
            }
            runCatching { connection.tcpNoDelay = true }
            // A connection that dies between accept and setup must not take the
            // accept loop — and so the whole server — down with it.
            val session = try {
                Session(
                    socket = connection,
                    appContext = appContext,
                    tokens = tokens,
                    audit = audit,
                    events = events,
                    settings = settings,
                    serverInfo = ::serverInfo,
                    onClosed = { finished ->
                        sessions.remove(finished)
                        publishClients()
                    },
                )
            } catch (e: Exception) {
                Log.w(TAG, "could not start a session", e)
                runCatching { connection.close() }
                continue
            }
            sessions.add(session)
            publishClients()
            audit.log(AuditLog.Kind.CONNECT, "connection accepted on $LOOPBACK:${socket.localPort}")
            scope.launch { session.run() }
        }
    }

    fun stop() {
        acceptJob?.cancel()
        acceptJob = null
        runCatching { serverSocket?.close() }
        serverSocket = null
        sessions.toList().forEach { it.close() }
        sessions.clear()
        _port.value = null
        publishClients()
    }

    fun shutdown() {
        stop()
        scope.cancel()
    }

    /** Sent on `auth` and `ping`, and shown on the Status screen. */
    fun serverInfo(): JSONObject = JSONObject()
        .put("server", "mmm")
        .put("version", BuildConfig.VERSION_NAME)
        .put("protocol", PROTOCOL_VERSION)
        .put("port", _port.value ?: -1)
        .put("model", Build.MODEL)
        .put("android_release", Build.VERSION.RELEASE)
        .put("sdk_int", Build.VERSION.SDK_INT)
        .put("root", Root.installed)
        .put("priv_app", Privileged.isPrivApp(appContext))
        .put("clients", sessions.size)
        .put("handle_cap", settings.current.handleCap)
        .put("proxy_reply_timeout_ms", settings.current.proxyReplyTimeoutMs)

    private fun publishClients() {
        _clients.value = sessions.map {
            ClientInfo(it.sessionId, it.clientLabel, it.clientId, it.connectedAt, it.handleCount)
        }
    }

    companion object {
        private const val TAG = "MmmServer"
        const val LOOPBACK = "127.0.0.1"
        const val PROTOCOL_VERSION = 1
        private const val BACKLOG = 8
    }
}
