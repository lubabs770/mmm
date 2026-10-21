package lubabs770.mmm.modemizer.rpc

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.provider.CallLog
import android.provider.ContactsContract
import android.provider.Telephony
import android.telephony.SmsManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat

/**
 * The handful of operations a *scoped* token is expected to need.
 *
 * Everything here is reachable through plain reflection like any other class, so the
 * facade adds no powers — it exists so a scoped token (an AI assistant, say) can be
 * allowed `lubabs770.mmm.modemizer.rpc.Facade#*` and nothing else, instead of an
 * allow-list spread over a dozen framework classes. Full tokens can ignore it.
 */
object Facade {

    private const val NOTIFY_CHANNEL = "mmm-client"
    private const val NOTIFY_ID_BASE = 7300

    /** Sends [body] to [to], splitting it into parts if it is too long for one SMS. */
    @JvmStatic
    @Suppress("DEPRECATION")
    fun smsSend(context: Context, to: String, body: String): Boolean {
        val manager = if (Build.VERSION.SDK_INT >= 31) {
            context.getSystemService(SmsManager::class.java)
        } else {
            SmsManager.getDefault()
        } ?: return false
        val parts = manager.divideMessage(body)
        if (parts.size <= 1) {
            manager.sendTextMessage(to, null, body, null, null)
        } else {
            manager.sendMultipartTextMessage(to, null, parts, null, null)
        }
        return true
    }

    /** Most recent messages first: id, address, body, date (epoch ms), type. */
    @JvmStatic
    fun smsList(context: Context, limit: Int): List<Map<String, Any?>> {
        val columns = arrayOf(
            Telephony.Sms._ID,
            Telephony.Sms.ADDRESS,
            Telephony.Sms.BODY,
            Telephony.Sms.DATE,
            Telephony.Sms.TYPE,
            Telephony.Sms.READ,
        )
        return query(context, Telephony.Sms.CONTENT_URI, columns, null, null, Telephony.Sms.DATE, limit)
    }

    /** Most recent calls first: number, date, duration (seconds), type. */
    @JvmStatic
    fun callLog(context: Context, limit: Int): List<Map<String, Any?>> {
        val columns = arrayOf(
            CallLog.Calls._ID,
            CallLog.Calls.NUMBER,
            CallLog.Calls.CACHED_NAME,
            CallLog.Calls.DATE,
            CallLog.Calls.DURATION,
            CallLog.Calls.TYPE,
        )
        return query(context, CallLog.Calls.CONTENT_URI, columns, null, null, CallLog.Calls.DATE, limit)
    }

    /** Contacts whose name or number contains [term]. */
    @JvmStatic
    fun contactsSearch(context: Context, term: String, limit: Int): List<Map<String, Any?>> {
        val columns = arrayOf(
            ContactsContract.CommonDataKinds.Phone._ID,
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
        )
        val like = "%$term%"
        val selection =
            "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ? OR " +
                "${ContactsContract.CommonDataKinds.Phone.NUMBER} LIKE ?"
        return query(
            context,
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            columns,
            selection,
            arrayOf(like, like),
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            limit,
        )
    }

    /** Signal, battery, network and SIM state in one call. */
    @JvmStatic
    // READ_PHONE_STATE is checked right here; lint cannot follow that through the
    // runCatching lambdas the calls sit in.
    @SuppressLint("MissingPermission")
    fun deviceStatus(context: Context): Map<String, Any?> {
        val out = LinkedHashMap<String, Any?>()
        out["model"] = Build.MODEL
        out["manufacturer"] = Build.MANUFACTURER
        out["androidRelease"] = Build.VERSION.RELEASE
        out["sdkInt"] = Build.VERSION.SDK_INT

        val tm = context.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager
        if (tm != null) {
            out["networkOperator"] = runCatching { tm.networkOperatorName }.getOrNull()
            out["simState"] = runCatching { tm.simState }.getOrNull()
            // READ_PHONE_STATE may simply not be granted: report null rather than
            // leaning on the SecurityException.
            val granted = ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.READ_PHONE_STATE,
            ) == PackageManager.PERMISSION_GRANTED
            out["phoneStatePermission"] = granted
            out["dataEnabled"] = if (granted && Build.VERSION.SDK_INT >= 26) {
                runCatching { tm.isDataEnabled }.getOrNull()
            } else {
                null
            }
            out["signalLevel"] = if (granted && Build.VERSION.SDK_INT >= 28) {
                runCatching { tm.signalStrength?.level }.getOrNull()
            } else {
                null
            }
        }

        val battery = context.getSystemService(Context.BATTERY_SERVICE) as? android.os.BatteryManager
        if (battery != null) {
            out["batteryPercent"] = runCatching {
                battery.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY)
            }.getOrNull()
            out["charging"] = runCatching {
                if (Build.VERSION.SDK_INT >= 26) battery.isCharging else null
            }.getOrNull()
        }
        return out
    }

    /** Posts a notification on the phone. Returns the notification id. */
    @JvmStatic
    fun notify(context: Context, title: String, text: String): Int {
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            manager.createNotificationChannel(
                NotificationChannel(NOTIFY_CHANNEL, "Client messages", NotificationManager.IMPORTANCE_DEFAULT)
            )
        }
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            android.app.Notification.Builder(context, NOTIFY_CHANNEL)
        } else {
            @Suppress("DEPRECATION")
            android.app.Notification.Builder(context)
        }
        val id = NOTIFY_ID_BASE + (System.currentTimeMillis() % 100).toInt()
        manager.notify(
            id,
            builder
                .setContentTitle(title)
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setAutoCancel(true)
                .build(),
        )
        return id
    }

    private fun query(
        context: Context,
        uri: android.net.Uri,
        columns: Array<String>,
        selection: String?,
        selectionArgs: Array<String>?,
        sortColumn: String,
        limit: Int,
    ): List<Map<String, Any?>> {
        val rows = ArrayList<Map<String, Any?>>()
        val capped = limit.coerceIn(1, 1000)
        context.contentResolver.query(
            uri, columns, selection, selectionArgs, "$sortColumn DESC LIMIT $capped"
        )?.use { cursor ->
            while (cursor.moveToNext()) {
                val row = LinkedHashMap<String, Any?>()
                for (i in 0 until cursor.columnCount) {
                    row[cursor.getColumnName(i)] = when (cursor.getType(i)) {
                        android.database.Cursor.FIELD_TYPE_NULL -> null
                        android.database.Cursor.FIELD_TYPE_INTEGER -> cursor.getLong(i)
                        android.database.Cursor.FIELD_TYPE_FLOAT -> cursor.getDouble(i)
                        android.database.Cursor.FIELD_TYPE_BLOB -> cursor.getBlob(i)
                        else -> cursor.getString(i)
                    }
                }
                rows.add(row)
            }
        }
        return rows
    }
}
