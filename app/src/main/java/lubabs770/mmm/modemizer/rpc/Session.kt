package lubabs770.mmm.modemizer.rpc

import android.content.Context
import android.util.Log
import java.io.BufferedWriter
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStreamWriter
import java.net.Socket
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.auth.ClientToken
import lubabs770.mmm.modemizer.auth.TokenStore
import lubabs770.mmm.modemizer.events.EventHub
import lubabs770.mmm.modemizer.events.EventTarget
import lubabs770.mmm.modemizer.events.ProxyFactory
import lubabs770.mmm.modemizer.settings.SettingsStore
import lubabs770.mmm.modemizer.util.Ids
import org.json.JSONObject

/**
 * One connection: newline-delimited JSON in, results and events out.
 *
 * The first message must be `auth`; anything else closes the connection. After that,
 * cheap operations (`release`, `reply`, `unsubscribe`, `ping`) run on the reader
 * thread and real calls go to a worker pool, so several can be in flight at once —
 * which is why every request carries an `id` that replies echo. `main: true` runs a
 * call on the main Looper for APIs that insist on it.
 */
class Session(
    private val socket: Socket,
    private val appContext: Context,
    private val tokens: TokenStore,
    private val audit: AuditLog,
    private val events: EventHub,
    private val settings: SettingsStore,
    private val serverInfo: () -> JSONObject,
    private val onClosed: (Session) -> Unit,
) : EventTarget {

    /** A value that is already JSON and should be sent through untouched. */
    class Prepared(val json: Any)

    override val sessionId: Long = Ids.nextSession()

    private val handles = HandleTable(settings.current.handleCap)
    // Calls block (content providers, binder, proxies awaiting replies), so they run on
    // the IO pool rather than the few CPU threads of Dispatchers.Default.
    private val workers = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val pendingReplies = ConcurrentHashMap<Long, ArrayBlockingQueue<Array<Any?>>>()
    private val writeLock = Any()
    private val closeLock = Any()
    private val writer: BufferedWriter =
        BufferedWriter(OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8))

    @Volatile
    private var closed = false

    @Volatile
    private var client: ClientToken? = null

    val connectedAt: Long = System.currentTimeMillis()
    val clientLabel: String get() = client?.label ?: "(unauthenticated)"
    val clientId: String? get() = client?.id
    val handleCount: Int get() = handles.size

    override val alive: Boolean get() = !closed && !socket.isClosed

    // ------------------------------------------------------------------ loop

    /** True once `auth` has succeeded on this connection. */
    val authenticated: Boolean get() = client != null

    /** Blocks until the connection ends; the server runs this on its own thread. */
    fun run() {
        try {
            // Until auth arrives, anyone on the phone can be on the other end: give
            // them a hard deadline and a small line, so idle, trickling or endless
            // connections cannot pin threads or memory.
            workers.launch {
                delay(AUTH_TIMEOUT_MS)
                if (client == null) {
                    audit.log(AuditLog.Kind.AUTH_FAIL, "no auth within ${AUTH_TIMEOUT_MS / 1000}s; connection closed")
                    close()
                }
            }
            val reader = LineReader(socket.getInputStream())
            while (!closed) {
                val limit = if (client == null) MAX_LINE_BEFORE_AUTH else MAX_LINE
                val line = try {
                    reader.readLine(limit)
                } catch (e: LineTooLong) {
                    throw e
                } catch (e: IOException) {
                    null // closed or reset
                } ?: break
                if (line.isBlank()) continue
                val request = try {
                    JSONObject(line)
                } catch (e: Exception) {
                    sendError(null, "protocol", "not a JSON object: ${e.message}", null)
                    continue
                }
                onRequest(request)
            }
        } catch (e: LineTooLong) {
            sendError(null, "protocol", e.message ?: "line too long", null)
        } catch (e: Exception) {
            Log.w(TAG, "session $sessionId ended", e)
        } finally {
            close()
        }
    }

    private class LineTooLong(limit: Int) : IOException("request line longer than $limit bytes")

    /** Reads `\n`-terminated UTF-8 lines, refusing any longer than the limit given. */
    private class LineReader(private val input: InputStream) {
        private val chunk = ByteArray(64 * 1024)
        private var start = 0
        private var end = 0

        /** The next line, or null at end of stream. */
        fun readLine(limit: Int): String? {
            val line = ByteArrayOutputStream()
            while (true) {
                if (start == end) {
                    val n = input.read(chunk)
                    if (n < 0) return if (line.size() == 0) null else line.toString("UTF-8")
                    start = 0
                    end = n
                }
                var newline = -1
                for (i in start until end) {
                    if (chunk[i] == '\n'.code.toByte()) {
                        newline = i
                        break
                    }
                }
                val stop = if (newline >= 0) newline else end
                if (line.size() + (stop - start) > limit) throw LineTooLong(limit)
                line.write(chunk, start, stop - start)
                if (newline >= 0) {
                    start = newline + 1
                    return line.toString("UTF-8")
                }
                start = end
            }
        }
    }

    private fun onRequest(request: JSONObject) {
        val id = request.opt("id")
        val op = request.optString("op")
        val current = client
        if (current != null && tokens.byId(current.id) == null) {
            // Revoked while connected. RpcServer also drops these sessions, this is
            // the backstop for a request already on its way.
            sendError(id, "unauthorized", "token revoked", null)
            close()
            return
        }
        if (current == null) {
            if (op != "auth") {
                audit.log(AuditLog.Kind.AUTH_FAIL, "'$op' before auth; connection closed")
                sendError(id, "unauthenticated", "the first message must be auth", null)
                close()
            } else {
                authenticate(id, request)
            }
            return
        }
        when (op) {
            // Cheap and order-sensitive: handled on the reader thread.
            "release", "reply", "unsubscribe", "ping" -> complete(id, request, op, current)
            else -> {
                val onMain = request.optBoolean("main", false)
                // IO, not Default: the scope's dispatcher would be replaced by whatever is
                // passed here, and blocking calls must not sit on the few CPU threads.
                workers.launch(if (onMain) Dispatchers.Main else Dispatchers.IO) {
                    complete(id, request, op, current)
                }
            }
        }
    }

    private fun complete(id: Any?, request: JSONObject, op: String, caller: ClientToken) {
        try {
            if (wantsExpand(request) && !caller.scope.full) {
                // Expanding calls every getter on an object, none of them scope-checked.
                audit.log(AuditLog.Kind.REJECT, "$op with expand outside scope", caller.id)
                throw SecurityException("expand requires a full-scope token")
            }
            sendResult(id, execute(op, request, caller), request)
        } catch (t: Throwable) {
            sendError(id, t)
        }
    }

    // ------------------------------------------------------------------- ops

    private fun execute(op: String, request: JSONObject, caller: ClientToken): Any? = when (op) {
        "ping" -> Prepared(serverInfo())

        "context" -> appContext

        // Returns the target itself; the result is expanded to a JSON snapshot.
        "expand" -> target(request)

        "cls" -> {
            val cls = RpcEngine.resolveClass(request.getString("name"))
            Prepared(
                JSONObject()
                    .put(Marshal.KEY_HANDLE, handles.put(cls))
                    .put("cls", "java.lang.Class")
                    .put("name", cls.name)
            )
        }

        "new" -> {
            val className = request.getString("cls")
            requireScope(caller, className, "<init>")
            audit.countCall(caller.id)
            RpcEngine.construct(className, args(request), sig(request), publicOnly = !caller.scope.full)
        }

        "call" -> {
            val receiver = target(request)
            val method = request.getString("method")
            requireScope(caller, RpcEngine.targetClassName(receiver), method)
            audit.countCall(caller.id)
            RpcEngine.call(receiver, method, args(request), sig(request), publicOnly = !caller.scope.full)
        }

        "get" -> {
            val receiver = target(request)
            val field = request.getString("field")
            requireScope(caller, RpcEngine.targetClassName(receiver), field)
            RpcEngine.getField(receiver, field, publicOnly = !caller.scope.full)
        }

        "set" -> {
            val receiver = target(request)
            val field = request.getString("field")
            requireScope(caller, RpcEngine.targetClassName(receiver), field)
            RpcEngine.setField(
                receiver,
                field,
                Marshal.fromJson(request.opt("value"), ::resolve),
                publicOnly = !caller.scope.full,
            )
            true
        }

        "release" -> {
            val list = request.optJSONArray("handles")
            var freed = 0
            if (list != null) {
                for (i in 0 until list.length()) if (handles.release(list.optLong(i))) freed++
            }
            freed
        }

        "proxy" -> {
            val names = request.optJSONArray("interfaces")
                ?: throw IllegalArgumentException("proxy needs 'interfaces'")
            val interfaces = (0 until names.length()).map { names.getString(it) }
            if (!caller.scope.full) {
                // A proxy can reach anything the listener's own API exposes.
                throw SecurityException("proxy requires a full-scope token")
            }
            val created = ProxyFactory.create(this, interfaces, settings.current.proxyReplyTimeoutMs)
            Prepared(
                JSONObject()
                    .put(Marshal.KEY_HANDLE, handles.put(created.proxy))
                    .put("cls", interfaces.first())
                    .put("sub", created.subscription)
            )
        }

        "subscribe" -> {
            val name = request.getString("name")
            val actionsJson = request.optJSONArray("action")
                ?: request.optJSONArray("actions")
                ?: throw IllegalArgumentException("subscribe needs 'action'")
            val actions = (0 until actionsJson.length()).map { actionsJson.getString(it) }
            actions.forEach { action ->
                if (!caller.scope.allowsAction(action)) {
                    audit.log(AuditLog.Kind.REJECT, "subscribe $action outside scope", caller.id)
                    throw SecurityException("token may not subscribe to $action")
                }
            }
            val durable = request.optBoolean("durable", false)
            events.subscribe(this, caller.id, name, actions, durable)
            // Live events wait in the backlog until it has been replayed, so nothing
            // newer can overtake something older. Drain until the backlog is empty;
            // only then does the hub attach this session for live delivery.
            var replayed = 0
            while (true) {
                val batch = events.drainOrAttach(this, caller.id, name)
                if (batch.isEmpty()) break
                batch.forEachIndexed { index, entry ->
                    val payload = runCatching { JSONObject(entry.json) }.getOrNull() ?: JSONObject()
                    try {
                        emit(name, entry.eventId, entry.ts, payload)
                    } catch (e: IOException) {
                        // The client went away mid-replay: keep what it did not get.
                        events.requeue(caller.id, name, batch.drop(index))
                        throw e
                    }
                }
                replayed += batch.size
            }
            Prepared(
                JSONObject()
                    .put("subscribed", name)
                    .put("durable", durable)
                    .put("replayed", replayed)
            )
        }

        "unsubscribe" -> events.unsubscribe(caller.id, request.getString("name"))

        "reply" -> {
            val eventId = request.getLong("event_id")
            val value = Marshal.fromJson(request.opt("value"), ::resolve)
            // False means the proxy call had already timed out and moved on.
            pendingReplies[eventId]?.offer(arrayOf(value)) ?: false
        }

        else -> throw IllegalArgumentException("unknown op '$op'")
    }

    private fun args(request: JSONObject): List<Any?> =
        Marshal.argsFromJson(request.optJSONArray("args"), ::resolve)

    private fun sig(request: JSONObject): List<String>? {
        val arr = request.optJSONArray("sig") ?: return null
        return (0 until arr.length()).map { arr.getString(it) }
    }

    private fun target(request: JSONObject): Any = when (val raw = request.opt("target")) {
        null, JSONObject.NULL -> throw IllegalArgumentException("missing 'target'")
        is String -> RpcEngine.resolveClass(raw)
        is Number -> handles.require(raw.toLong())
        is JSONObject -> Marshal.fromJson(raw, ::resolve)
            ?: throw IllegalArgumentException("'target' is null")
        else -> throw IllegalArgumentException("'target' must be a handle, class name or {\"\$cls\": …}")
    }

    private fun resolve(handle: Long): Any = handles.require(handle)

    private fun requireScope(caller: ClientToken, className: String, member: String) {
        if (caller.scope.allowsMethod(className, member)) return
        audit.log(AuditLog.Kind.REJECT, "$className#$member outside scope", caller.id)
        throw SecurityException("token is not allowed to call $className#$member")
    }

    // ------------------------------------------------------------------ auth

    private fun authenticate(id: Any?, request: JSONObject) {
        val token = request.optString("token")
        val found = if (token.isEmpty()) null else tokens.verify(token)
        if (found == null) {
            audit.log(AuditLog.Kind.AUTH_FAIL, "token rejected")
            sendError(id, "unauthorized", "unknown or revoked token", null)
            close()
            return
        }
        client = found
        tokens.touch(found.id)
        audit.log(AuditLog.Kind.AUTH_OK, "${found.label} connected, scope ${found.scope.describe()}", found.id)
        sendResult(
            id,
            Prepared(
                serverInfo()
                    .put("client", found.label)
                    .put("client_id", found.id)
                    .put("scope", found.scope.toJson())
            ),
            null,
        )
    }

    // ---------------------------------------------------------------- events

    override fun handle(value: Any): Long = handles.put(value)

    override fun emit(sub: String, eventId: Long, ts: Long, payload: Any) {
        val sent = send(
            JSONObject()
                .put("op", "event")
                .put("sub", sub)
                .put("event_id", eventId)
                .put("ts", ts)
                .put("payload", payload)
        )
        // EventHub buffers durable events on failure, so it has to hear about it.
        if (!sent) throw IOException("session $sessionId is closed")
    }

    override fun emitAwaitingReply(sub: String, payload: Any, timeoutMs: Long): Any? {
        val eventId = Ids.nextEvent()
        val mailbox = ArrayBlockingQueue<Array<Any?>>(1)
        pendingReplies[eventId] = mailbox
        return try {
            val sent = send(
                JSONObject()
                    .put("op", "event")
                    .put("sub", sub)
                    .put("event_id", eventId)
                    .put("ts", System.currentTimeMillis())
                    .put("needs_reply", true)
                    .put("payload", payload)
            )
            if (!sent) return null
            mailbox.poll(timeoutMs, TimeUnit.MILLISECONDS)?.get(0)
        } finally {
            pendingReplies.remove(eventId)
        }
    }

    // ------------------------------------------------------------------ wire

    private fun wantsExpand(request: JSONObject): Boolean =
        request.optBoolean(EXPAND, false) ||
            request.optBoolean("expand", false) ||
            request.optString("op") == "expand"

    private fun sendResult(id: Any?, value: Any?, request: JSONObject?) {
        val expand = request != null && wantsExpand(request)
        // If marshalling fails halfway, free the handles it already handed out: the
        // client never sees them, so it could never release them itself.
        val allocated = ArrayList<Long>()
        val sink = object : HandleSink {
            override fun handle(value: Any): Long = handles.put(value).also { allocated += it }
        }
        val json = try {
            when {
                value is Prepared -> value.json
                expand -> Marshal.expand(value, sink)
                else -> Marshal.toJson(value, sink)
            }
        } catch (t: Throwable) {
            allocated.forEach { handles.release(it) }
            throw t
        }
        send(JSONObject().put("op", "result").put("id", id ?: JSONObject.NULL).put("value", json))
    }

    private fun sendError(id: Any?, throwable: Throwable) {
        val stack = throwable.stackTraceToString().let {
            if (it.length > MAX_STACK) it.substring(0, MAX_STACK) else it
        }
        val message = when (throwable) {
            is Overloads.Ambiguous -> throwable.message
            else -> throwable.message ?: throwable.javaClass.simpleName
        }
        sendError(id, throwable.javaClass.name, message ?: "", stack)
    }

    private fun sendError(id: Any?, type: String, message: String, stack: String?) {
        send(
            JSONObject()
                .put("op", "error")
                .put("id", id ?: JSONObject.NULL)
                .put("type", type)
                .put("message", message)
                .put("java_stack", stack ?: JSONObject.NULL)
        )
    }

    /** False when the message did not go out, because the session is closed or just died. */
    private fun send(obj: JSONObject): Boolean {
        if (closed) return false
        return try {
            synchronized(writeLock) {
                writer.write(obj.toString())
                writer.write("\n")
                writer.flush()
            }
            true
        } catch (e: IOException) {
            // The client vanished mid-write; the reader loop will notice too.
            close()
            false
        }
    }

    fun close() {
        synchronized(closeLock) {
            if (closed) return
            closed = true
        }
        events.detach(this)
        pendingReplies.values.forEach { it.offer(arrayOf<Any?>(null)) }
        pendingReplies.clear()
        handles.clear()
        runCatching { socket.close() }
        workers.cancel()
        client?.let { audit.log(AuditLog.Kind.CONNECT, "${it.label} disconnected", it.id) }
        onClosed(this)
    }

    companion object {
        private const val TAG = "MmmSession"
        private const val MAX_STACK = 4000
        private const val EXPAND = "\$expand"
        private const val AUTH_TIMEOUT_MS = 10_000L
        private const val MAX_LINE_BEFORE_AUTH = 64 * 1024
        private const val MAX_LINE = 32 * 1024 * 1024
    }
}
