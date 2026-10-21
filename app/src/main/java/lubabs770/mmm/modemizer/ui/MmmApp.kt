package lubabs770.mmm.modemizer.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.flow.MutableStateFlow

enum class Screen(val title: String, val digit: Int, val icon: ImageVector) {
    STATUS("Status", 1, Icons.Filled.Info),
    CLIENTS("Clients", 2, Icons.Filled.Person),
    PERMISSIONS("Permissions", 3, Icons.Filled.Lock),
    SUBSCRIPTIONS("Subscriptions", 4, Icons.Filled.Notifications),
    ACTIVITY("Activity", 5, Icons.Filled.List),
    SETTINGS("Settings", 6, Icons.Filled.Settings),
}

/**
 * Keypad input arrives at the Activity, not at a composable, so the two meet here:
 * number keys request a screen, and back or the left softkey asks to go up.
 */
object NavBus {
    val requested = MutableStateFlow<Screen?>(null)
    val upRequests = MutableStateFlow(0)

    /** True when "up" means something: a compact screen showing a detail. */
    val canGoUp = MutableStateFlow(false)

    fun request(screen: Screen) {
        requested.value = screen
    }

    fun goUp() {
        upRequests.value += 1
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MmmApp() {
    val configuration = LocalConfiguration.current
    // A keypad phone is about 240x320dp: too small for a bottom bar, so it gets a
    // top-level list and one screen at a time.
    val compact = configuration.screenWidthDp < 360 || configuration.screenHeightDp < 420

    var screen by remember { mutableStateOf(if (compact) null else Screen.STATUS) }
    val requested by NavBus.requested.collectAsStateWithLifecycle()
    val upRequests by NavBus.upRequests.collectAsStateWithLifecycle()
    val primaryFocus = remember { FocusRequester() }

    LaunchedEffect(requested) {
        val target = requested
        if (target != null) {
            screen = target
            NavBus.requested.value = null
        }
    }
    LaunchedEffect(upRequests) {
        if (upRequests > 0 && compact) screen = null
    }
    LaunchedEffect(screen) {
        // Put the D-pad on this screen's primary action.
        runCatching { primaryFocus.requestFocus() }
    }
    LaunchedEffect(screen, compact) {
        NavBus.canGoUp.value = compact && screen != null
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(screen?.title ?: "MMM") },
                navigationIcon = {
                    if (compact && screen != null) {
                        IconButton(
                            onClick = { screen = null },
                            modifier = Modifier.focusHighlight(),
                        ) {
                            Icon(Icons.Filled.ArrowBack, contentDescription = "Back")
                        }
                    }
                },
            )
        },
        bottomBar = {
            if (!compact) {
                NavigationBar {
                    Screen.entries.forEach { item ->
                        NavigationBarItem(
                            selected = screen == item,
                            onClick = { screen = item },
                            icon = { Icon(item.icon, contentDescription = item.title) },
                            label = { Text("${item.digit} ${item.title}", maxLines = 1) },
                            modifier = Modifier.focusHighlight(),
                        )
                    }
                }
            }
        },
    ) { insets ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(insets)
                .padding(screenPadding(compact)),
        ) {
            when (screen) {
                null -> TopLevelList(onPick = { screen = it }, focusRequester = primaryFocus)
                Screen.STATUS -> StatusScreen(primaryFocus)
                Screen.CLIENTS -> ClientsScreen(primaryFocus)
                Screen.PERMISSIONS -> PermissionsScreen(primaryFocus)
                Screen.SUBSCRIPTIONS -> SubscriptionsScreen(primaryFocus)
                Screen.ACTIVITY -> ActivityScreen(primaryFocus)
                Screen.SETTINGS -> SettingsScreen(primaryFocus)
            }
        }
    }
}

@Composable
private fun TopLevelList(onPick: (Screen) -> Unit, focusRequester: FocusRequester) {
    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Text(
            "Press a number key or pick a screen.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        Screen.entries.forEachIndexed { index, item ->
            ListItem(
                headlineContent = { Text("${item.digit}  ${item.title}") },
                leadingContent = { Icon(item.icon, contentDescription = null) },
                modifier = Modifier
                    .fillMaxWidth()
                    .focusHighlight()
                    .then(if (index == 0) Modifier.focusRequester(focusRequester) else Modifier)
                    .clickable { onPick(item) },
            )
        }
    }
}
