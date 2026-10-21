package lubabs770.mmm.modemizer.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import lubabs770.mmm.modemizer.settings.SettingsStore

/**
 * Starts the service after a reboot, so durable subscriptions are registered again
 * before anything interesting arrives. Honours the "start on boot" setting.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action != Intent.ACTION_BOOT_COMPLETED && action != Intent.ACTION_LOCKED_BOOT_COMPLETED) {
            return
        }
        if (!SettingsStore.get(context).current.startOnBoot) return
        MmmService.start(context)
    }
}
