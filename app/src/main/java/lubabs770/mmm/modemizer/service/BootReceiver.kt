package lubabs770.mmm.modemizer.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import lubabs770.mmm.modemizer.settings.SettingsStore

/**
 * Starts the service after a reboot, so durable subscriptions are registered again
 * before anything interesting arrives, and after the app is updated: installing an
 * update kills the process, and on a phone with no screen nothing else would bring
 * the listener back. Honours the "start on boot" setting for both.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action !in STARTS) return
        if (!SettingsStore.get(context).current.startOnBoot) return
        MmmService.start(context)
    }

    private companion object {
        // All three are exempt from the background foreground-service start limits.
        val STARTS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
        )
    }
}
