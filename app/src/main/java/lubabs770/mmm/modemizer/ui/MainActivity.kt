package lubabs770.mmm.modemizer.ui

import android.Manifest
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch
import lubabs770.mmm.modemizer.settings.SettingsStore

class MainActivity : ComponentActivity() {

    private val requestNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val backToList = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            NavBus.goUp()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        onBackPressedDispatcher.addCallback(this, backToList)
        lifecycleScope.launch {
            NavBus.canGoUp.collect { backToList.isEnabled = it }
        }
        if (Build.VERSION.SDK_INT >= 33) {
            requestNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        val settings = SettingsStore.get(this)

        setContent {
            val current by settings.state.collectAsStateWithLifecycle()
            MmmTheme(current.theme) {
                MmmApp()
            }
        }
    }

    /**
     * Keypad phones have no touch screen: number keys jump straight to a screen and
     * the softkeys stand in for back and "activate what is focused".
     */
    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        val screen = when (keyCode) {
            KeyEvent.KEYCODE_1 -> Screen.STATUS
            KeyEvent.KEYCODE_2 -> Screen.CLIENTS
            KeyEvent.KEYCODE_3 -> Screen.PERMISSIONS
            KeyEvent.KEYCODE_4 -> Screen.SUBSCRIPTIONS
            KeyEvent.KEYCODE_5 -> Screen.ACTIVITY
            KeyEvent.KEYCODE_6 -> Screen.SETTINGS
            else -> null
        }
        if (screen != null) {
            NavBus.request(screen)
            return true
        }
        if (keyCode == KeyEvent.KEYCODE_SOFT_LEFT) {
            onBackPressedDispatcher.onBackPressed()
            return true
        }
        if (keyCode == KeyEvent.KEYCODE_SOFT_RIGHT) {
            // Activate whatever has focus, the way a touch would.
            window.decorView.dispatchKeyEvent(
                KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_CENTER)
            )
            window.decorView.dispatchKeyEvent(
                KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_DPAD_CENTER)
            )
            return true
        }
        return super.onKeyDown(keyCode, event)
    }
}
