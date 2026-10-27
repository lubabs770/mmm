package lubabs770.mmm.modemizer.service

import android.content.Context
import android.os.Build
import android.util.Log
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
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
 * The listener on 127.0.0.1, and optionally on the phone's tailnet address.
 *
 * Loopback by default: a remote computer reaches it through an SSH tunnel or
 * `adb forward`, both of which bring their own authentication. The opt-in tailnet
 * listener leans on Tailscale's instead, binds only the Tailscale address, and drops
 * any peer outside 100.64.0.0/10 (Linux delivers a packet for any local address on
 * any interface, so the bind alone is not enough). Every connection still has to
 * present a token.
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
    private var tailnetSocket: ServerSocket? = null
    private var tailnetAcceptJob: Job? = null
    private var tailnetWatchJob: Job? = null
    @Volatile
    private var tailnetWanted = false

    private val _port = MutableStateFlow<Int?>(null)
    /** The port being listened on, or null when the server is down. */
    val port: StateFlow<Int?> = _port.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _tailnet = MutableStateFlow<String?>(null)
    /** `address:port` of the tailnet listener, or null when there is none. */
    val tailnet: StateFlow<String?> = _tailnet.asStateFlow()

    private val _clients = MutableStateFlow<List<ClientInfo>>(emptyList())
    val clients: StateFlow<List<ClientInfo>> = _clients.asStateFlow()

    fun start(port: Int, tailnet: Boolean = false) {
        if (_port.value == port && tailnetWanted == tailnet && acceptJob?.isActive == true) return
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
        acceptJob = scope.launch { acceptLoop(socket, tailnetOnly = false) }
        tailnetWanted = tailnet
        if (tailnet) tailnetWatchJob = scope.launch { watchTailnet(port) }
    }

    /**
     * Tailscale often comes up after MMM on boot, and its address can change, so the
     * tailnet listener follows the address instead of binding once.
     *
     * ponytail: polls every [TAILNET_POLL_MS]; a ConnectivityManager callback if the
     * delay ever matters.
     */
    private suspend fun watchTailnet(port: Int) {
        var bound: InetAddress? = null
        while (true) {
            val address = Tailnet.localAddress()
            if (address != bound) {
                closeTailnet()
                bound = address
                if (address != null) bindTailnet(address, port)
            }
            delay(TAILNET_POLL_MS)
        }
    }

    // Synchronized with [closeTailnet]: the watcher binds on an IO thread while
    // [stop] may run on another, and a socket bound after stop would leak.
    @Synchronized
    private fun bindTailnet(address: InetAddress, port: Int) {
        if (!tailnetWanted) return
        val where = "${address.hostAddress}:$port"
        val socket = try {
            ServerSocket(port, BACKLOG, address)
        } catch (e: Exception) {
            audit.log(AuditLog.Kind.ERROR, "listen on $where failed: ${e.message}")
            return
        }
        tailnetSocket = socket
        _tailnet.value = where
        audit.log(AuditLog.Kind.SERVICE, "listening on $where (tailnet)")
        tailnetAcceptJob = scope.launch { acceptLoop(socket, tailnetOnly = true) }
    }

    @Synchronized
    private fun closeTailnet() {
        tailnetAcceptJob?.cancel()
        tailnetAcceptJob = null
        runCatching { tailnetSocket?.close() }
        if (tailnetSocket != null) audit.log(AuditLog.Kind.SERVICE, "stopped listening on ${_tailnet.value}")
        tailnetSocket = null
        _tailnet.value = null
    }

    private suspend fun acceptLoop(socket: ServerSocket, tailnetOnly: Boolean) {
        while (!socket.isClosed) {
            val connection: Socket = try {
                socket.accept()
            } catch (e: Exception) {
                if (!socket.isClosed) Log.w(TAG, "accept failed", e)
                break
            }
            if (tailnetOnly && !Tailnet.contains(connection.inetAddress)) {
                // Logcat only, like the flood below: the Activity log is for real clients.
                Log.w(TAG, "refused ${connection.inetAddress.hostAddress}: not a tailnet peer")
                runCatching { connection.close() }
                continue
            }
            runCatching { connection.tcpNoDelay = true }
            if (sessions.count { !it.authenticated } >= MAX_UNAUTHENTICATED) {
                // Anything on the phone (or the tailnet) can open a connection; refuse a pile
                // of them that never authenticate. Logcat only: a flood of these must
                // not push real entries out of the Activity log.
                Log.w(TAG, "too many connections waiting to authenticate; refused one")
                runCatching { connection.close() }
                continue
            }
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
            audit.log(
                AuditLog.Kind.CONNECT,
                "connection accepted on ${socket.inetAddress.hostAddress}:${socket.localPort}" +
                    if (tailnetOnly) " from ${connection.inetAddress.hostAddress}" else "",
            )
            // A thread of its own, not a pooled coroutine: a session blocks on its
            // socket for its whole life and must not use up a shared pool.
            thread(name = "mmm-session-${session.sessionId}", isDaemon = true) { session.run() }
        }
    }

    /** Closes every live session of a token, for when it is revoked. */
    fun disconnectToken(tokenId: String) {
        sessions.filter { it.clientId == tokenId }.forEach { it.close() }
    }

    fun stop() {
        acceptJob?.cancel()
        acceptJob = null
        runCatching { serverSocket?.close() }
        serverSocket = null
        tailnetWatchJob?.cancel()
        tailnetWatchJob = null
        tailnetWanted = false
        closeTailnet()
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
        .put("tailnet", _tailnet.value ?: JSONObject.NULL)
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
        private const val MAX_UNAUTHENTICATED = 16
        private const val TAILNET_POLL_MS = 10_000L
    }
}
