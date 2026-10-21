package lubabs770.mmm.modemizer.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.launch
import lubabs770.mmm.modemizer.R
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.ui.MainActivity

/**
 * Keeps the RPC listener alive.
 *
 * A foreground service with a visible notification is the only way Android lets a
 * socket listener survive; the notification doubles as the "MMM is reachable" light
 * and carries a Stop action.
 */
class MmmService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startForegroundNotification(getString(R.string.app_name), "Starting…")
        MmmRuntime.start(this)
        AuditLog.get(this).log(AuditLog.Kind.SERVICE, "service started")

        val server = MmmRuntime.server(this)
        combine(server.port, server.clients) { port, clients ->
            when {
                port == null -> "Not listening"
                clients.isEmpty() -> "Listening on ${RpcServer.LOOPBACK}:$port"
                else -> "Listening on ${RpcServer.LOOPBACK}:$port · ${clients.size} client" +
                    if (clients.size == 1) "" else "s"
            }
        }
            .onEach { updateNotification(getString(R.string.app_name), it) }
            .launchIn(scope)

        scope.launch { whitelistFromBatteryOptimisation() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        AuditLog.get(this).log(AuditLog.Kind.SERVICE, "service stopped")
        MmmRuntime.stop(this)
        scope.cancel()
        super.onDestroy()
    }

    /**
     * Doze would otherwise freeze the listener while the screen is off. The user can
     * grant this by hand too; with root it needs no interaction.
     */
    private fun whitelistFromBatteryOptimisation() {
        if (!Root.installed) return
        val result = Root.run("dumpsys deviceidle whitelist +$packageName")
        if (result.ok) {
            AuditLog.get(this).log(AuditLog.Kind.SERVICE, "added to the battery whitelist")
        }
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.service_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.service_channel_desc)
            setShowBadge(false)
        }
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .createNotificationChannel(channel)
    }

    /** After the first startForeground, the notification is just re-posted. */
    private fun updateNotification(title: String, text: String) {
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIFICATION_ID, buildNotification(title, text))
    }

    private fun startForegroundNotification(title: String, text: String) {
        val notification = buildNotification(title, text)
        // specialUse only exists from API 34; earlier releases take the untyped call.
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(title: String, text: String): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or pendingIntentImmutableFlag(),
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, MmmService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or pendingIntentImmutableFlag(),
        )
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle(title)
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(null, "Stop", stop).build()
            )
            .build()
    }

    private fun pendingIntentImmutableFlag(): Int =
        if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0

    companion object {
        const val ACTION_STOP = "lubabs770.mmm.modemizer.STOP"
        private const val CHANNEL_ID = "mmm-service"
        private const val NOTIFICATION_ID = 7357

        fun start(context: Context) {
            val intent = Intent(context, MmmService::class.java)
            if (Build.VERSION.SDK_INT >= 26) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, MmmService::class.java))
        }
    }
}
