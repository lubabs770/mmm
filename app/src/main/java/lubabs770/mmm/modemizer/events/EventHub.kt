package lubabs770.mmm.modemizer.events

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Handler
import android.os.HandlerThread
import androidx.core.content.ContextCompat
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.rpc.Marshal
import lubabs770.mmm.modemizer.settings.SettingsStore
import lubabs770.mmm.modemizer.util.Ids
import lubabs770.mmm.modemizer.util.JsonFile
import org.json.JSONArray
import org.json.JSONObject

/**
 * Broadcast subscriptions, their backlogs, and the per-minute event rate.
 *
 * A subscription is named by the client and owned by its token, so two clients can
 * use the same name without colliding. A `durable` subscription outlives the
 * connection: its receiver stays registered, events pile up in a [Backlog], and the
 * whole set is re-registered when the service starts again — an SMS that arrives
 * while nobody is connected is still delivered later.
 *
 * Broadcast payloads are always plain JSON (see [Marshal]): a buffered event may be
 * replayed into a different session than the one that subscribed, so it must not
 * contain handles.
 */
class EventHub(
    private val appContext: Context,
    private val settings: SettingsStore,
    private val audit: AuditLog,
) {

    data class Info(
        val name: String,
        val tokenId: String,
        val actions: List<String>,
        val durable: Boolean,
        val attached: Boolean,
        val backlogSize: Int,
        val delivered: Long,
        val dropped: Long,
    )

    private class Sub(
        val name: String,
        val tokenId: String,
        var actions: List<String>,
        val durable: Boolean,
        val backlog: Backlog?,
    ) {
        var receiver: BroadcastReceiver? = null
        var target: EventTarget? = null
        var delivered = 0L
    }

    private val lock = Any()
    private val subs = LinkedHashMap<String, Sub>()
    private val recentEvents = ArrayDeque<Long>()
    private val file = File(appContext.filesDir, "subscriptions.json")

    @Volatile
    private var lastPersist = 0L

    private val deliveryHandler: Handler by lazy {
        Handler(HandlerThread("mmm-events").apply { start() }.looper)
    }

    private val _state = MutableStateFlow<List<Info>>(emptyList())
    val state: StateFlow<List<Info>> = _state.asStateFlow()

    private val _eventsPerMinute = MutableStateFlow(0)
    val eventsPerMinute: StateFlow<Int> = _eventsPerMinute.asStateFlow()

    // ------------------------------------------------------------ subscribe

    /** Registers or re-attaches a subscription and returns any backlog to replay. */
    fun subscribe(
        target: EventTarget,
        tokenId: String,
        name: String,
        actions: List<String>,
        durable: Boolean,
    ): List<Backlog.Entry> {
        require(name.isNotBlank()) { "subscription needs a name" }
        require(actions.isNotEmpty()) { "subscription needs at least one action" }
        val replay: List<Backlog.Entry>
        synchronized(lock) {
            val key = key(tokenId, name)
            var sub = subs[key]
            if (sub != null && sub.durable != durable) {
                // The client changed its mind about durability: start the subscription over.
                unregister(sub)
                subs.remove(key)
                sub = null
            }
            if (sub == null) {
                sub = Sub(name, tokenId, actions, durable, if (durable) newBacklog() else null)
                register(sub)
                subs[key] = sub
            } else if (sub.actions != actions) {
                unregister(sub)
                sub.actions = actions
                register(sub)
            }
            sub.target = target
            replay = sub.backlog?.drain() ?: emptyList()
        }
        audit.log(
            AuditLog.Kind.SUBSCRIBE,
            "$name ${if (durable) "(durable) " else ""}→ ${actions.joinToString(", ")}",
            tokenId,
        )
        persist(force = true)
        publish()
        return replay
    }

    fun unsubscribe(tokenId: String, name: String): Boolean {
        val removed = synchronized(lock) {
            val sub = subs.remove(key(tokenId, name)) ?: return@synchronized false
            unregister(sub)
            true
        }
        if (removed) {
            persist(force = true)
            publish()
        }
        return removed
    }

    /** Called when a connection ends: durable subscriptions stay, the rest go. */
    fun detach(target: EventTarget) {
        synchronized(lock) {
            val gone = ArrayList<String>()
            subs.forEach { (key, sub) ->
                if (sub.target?.sessionId == target.sessionId) {
                    sub.target = null
                    if (!sub.durable) {
                        unregister(sub)
                        gone += key
                    }
                }
            }
            gone.forEach { subs.remove(it) }
        }
        publish()
    }

    /** Revoking a token takes its durable subscriptions with it. */
    fun dropForToken(tokenId: String) {
        synchronized(lock) {
            val gone = subs.filterValues { it.tokenId == tokenId }
            gone.forEach { (key, sub) ->
                unregister(sub)
                subs.remove(key)
            }
        }
        persist(force = true)
        publish()
    }

    /** Returns events a client was sent during replay but never received. */
    fun requeue(tokenId: String, name: String, undelivered: List<Backlog.Entry>) {
        if (undelivered.isEmpty()) return
        synchronized(lock) { subs[key(tokenId, name)]?.backlog?.requeue(undelivered) }
        persist(force = true)
        publish()
    }

    fun clearBacklog(tokenId: String, name: String) {
        synchronized(lock) { subs[key(tokenId, name)]?.backlog?.clear() }
        persist(force = true)
        publish()
    }

    fun infos(): List<Info> = _state.value

    // ------------------------------------------------------------- lifecycle

    /** Re-registers durable subscriptions after a service or device restart. */
    fun restore() {
        val obj = JsonFile.read(file) ?: return
        val arr = obj.optJSONArray("subs") ?: return
        synchronized(lock) {
            for (i in 0 until arr.length()) {
                val item = arr.optJSONObject(i) ?: continue
                val name = item.optString("name")
                val tokenId = item.optString("tokenId")
                if (name.isEmpty() || tokenId.isEmpty()) continue
                val actions = item.optJSONArray("actions")?.let { a ->
                    (0 until a.length()).map { a.optString(it) }
                }.orEmpty().filter { it.isNotEmpty() }
                if (actions.isEmpty()) continue
                val sub = Sub(name, tokenId, actions, durable = true, backlog = newBacklog())
                item.optJSONArray("backlog")?.let { entries ->
                    val restored = (0 until entries.length()).mapNotNull { idx ->
                        val e = entries.optJSONObject(idx) ?: return@mapNotNull null
                        Backlog.Entry(e.optLong("eventId"), e.optLong("ts"), e.optString("json"))
                    }
                    sub.backlog?.restore(restored)
                }
                register(sub)
                subs[key(tokenId, name)] = sub
            }
        }
        publish()
    }

    fun shutdown() {
        synchronized(lock) {
            subs.values.forEach { unregister(it) }
        }
        persist(force = true)
    }

    // -------------------------------------------------------------- internals

    private fun newBacklog(): Backlog {
        val s = settings.current
        return Backlog(
            maxEvents = s.backlogEvents.coerceAtLeast(1),
            maxAgeMs = s.backlogHours.toLong() * 60 * 60 * 1000,
        )
    }

    private fun register(sub: Sub) {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                if (intent != null) deliver(sub, intent)
            }
        }
        val filter = IntentFilter().apply { sub.actions.forEach { addAction(it) } }
        // System broadcasts come from outside the app, so the receiver has to be exported.
        // Delivery writes to a socket and to disk, so it runs on its own thread: on the
        // main thread a client that stops reading would freeze the whole app.
        ContextCompat.registerReceiver(
            appContext,
            receiver,
            filter,
            null,
            deliveryHandler,
            ContextCompat.RECEIVER_EXPORTED,
        )
        sub.receiver = receiver
    }

    private fun unregister(sub: Sub) {
        sub.receiver?.let { runCatching { appContext.unregisterReceiver(it) } }
        sub.receiver = null
    }

    private fun deliver(sub: Sub, intent: Intent) {
        val payload = runCatching { Marshal.intentToJson(intent, null) }.getOrElse { e ->
            JSONObject().put("action", intent.action).put("error", e.message)
        }
        val eventId = Ids.nextEvent()
        val ts = System.currentTimeMillis()
        val target = synchronized(lock) {
            sub.delivered++
            sub.target
        }
        if (target != null && target.alive) {
            runCatching { target.emit(sub.name, eventId, ts, payload) }
                .onFailure { sub.backlog?.add(Backlog.Entry(eventId, ts, payload.toString())) }
        } else {
            sub.backlog?.add(Backlog.Entry(eventId, ts, payload.toString()))
        }
        recordRate(ts)
        persist(force = false)
        publish()
    }

    private fun recordRate(ts: Long) {
        synchronized(recentEvents) {
            recentEvents.addLast(ts)
            val cutoff = ts - 60_000
            while (recentEvents.isNotEmpty() && recentEvents.first() < cutoff) recentEvents.removeFirst()
            _eventsPerMinute.value = recentEvents.size
        }
    }

    private fun publish() {
        _state.value = synchronized(lock) {
            subs.values.map { sub ->
                Info(
                    name = sub.name,
                    tokenId = sub.tokenId,
                    actions = sub.actions,
                    durable = sub.durable,
                    attached = sub.target?.alive == true,
                    backlogSize = sub.backlog?.size ?: 0,
                    delivered = sub.delivered,
                    dropped = sub.backlog?.dropped ?: 0L,
                )
            }
        }
    }

    /** Durable subscriptions and their backlogs, written at most every few seconds. */
    private fun persist(force: Boolean) {
        val now = System.currentTimeMillis()
        if (!force && now - lastPersist < PERSIST_MS) return
        lastPersist = now
        val arr = JSONArray()
        synchronized(lock) {
            subs.values.filter { it.durable }.forEach { sub ->
                val entries = JSONArray()
                sub.backlog?.snapshot()?.forEach { e ->
                    entries.put(
                        JSONObject().put("eventId", e.eventId).put("ts", e.ts).put("json", e.json)
                    )
                }
                arr.put(
                    JSONObject()
                        .put("name", sub.name)
                        .put("tokenId", sub.tokenId)
                        .put("actions", JSONArray(sub.actions))
                        .put("backlog", entries)
                )
            }
        }
        JsonFile.write(file, JSONObject().put("subs", arr))
    }

    private fun key(tokenId: String, name: String) = "$tokenId/$name"

    companion object {
        private const val PERSIST_MS = 3_000L
    }
}
