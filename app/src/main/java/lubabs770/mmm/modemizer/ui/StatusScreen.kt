package lubabs770.mmm.modemizer.ui

import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import lubabs770.mmm.modemizer.BuildConfig
import lubabs770.mmm.modemizer.service.MmmRuntime
import lubabs770.mmm.modemizer.service.MmmService
import lubabs770.mmm.modemizer.service.Privileged
import lubabs770.mmm.modemizer.service.Root
import lubabs770.mmm.modemizer.service.RpcServer

@Composable
fun StatusScreen(primary: FocusRequester) {
    val context = LocalContext.current
    val server = remember { MmmRuntime.server(context) }
    val events = remember { MmmRuntime.events(context) }

    val port by server.port.collectAsStateWithLifecycle()
    val error by server.error.collectAsStateWithLifecycle()
    val clients by server.clients.collectAsStateWithLifecycle()
    val eventsPerMinute by events.eventsPerMinute.collectAsStateWithLifecycle()

    var rootGranted by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(Unit) {
        // `su` can take a moment and may prompt, so never on the main thread.
        rootGranted = withContext(Dispatchers.IO) { if (Root.installed) Root.granted() else false }
    }

    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        SectionCard("Service") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    if (port != null) "Running" else "Stopped",
                    style = MaterialTheme.typography.bodyLarge,
                )
                Switch(
                    checked = port != null,
                    onCheckedChange = { on ->
                        if (on) MmmService.start(context) else MmmService.stop(context)
                    },
                    modifier = Modifier.focusRequester(primary).focusHighlight(),
                )
            }
            InfoRow("Address", port?.let { "${RpcServer.LOOPBACK}:$it" } ?: "—")
            InfoRow("Connected clients", clients.size.toString())
            InfoRow("Events per minute", eventsPerMinute.toString())
            error?.let { Hint(it) }
            if (port == null) {
                Hint("Nothing is listening. Turn the service on, then pair a client.")
            }
        }

        SectionCard("Device") {
            InfoRow("Model", Build.MODEL)
            InfoRow("Android", "${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
            InfoRow(
                "Root",
                when {
                    rootGranted == true -> "granted"
                    Root.installed -> "su present, not granted"
                    else -> "no"
                },
            )
            InfoRow("Priv-app", if (Privileged.isPrivApp(context)) "yes" else "no")
            InfoRow("Version", BuildConfig.VERSION_NAME)
        }

        SectionCard("Live connections") {
            if (clients.isEmpty()) {
                Hint("No client is connected right now.")
            } else {
                clients.forEach { client ->
                    InfoRow(
                        client.label,
                        "${formatAge(client.since)} · ${client.handles} handles",
                    )
                }
            }
        }
    }
}
