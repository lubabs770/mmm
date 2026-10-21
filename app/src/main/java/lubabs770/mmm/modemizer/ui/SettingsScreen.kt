package lubabs770.mmm.modemizer.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import lubabs770.mmm.modemizer.service.MmmRuntime
import lubabs770.mmm.modemizer.settings.ThemeMode

@Composable
fun SettingsScreen(primary: FocusRequester) {
    val context = LocalContext.current
    val store = remember { MmmRuntime.settings(context) }
    val settings by store.state.collectAsStateWithLifecycle()

    var portText by remember(settings.port) { mutableStateOf(settings.port.toString()) }
    var portNote by remember { mutableStateOf<String?>(null) }

    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        SectionCard("Listener") {
            OutlinedTextField(
                value = portText,
                onValueChange = { portText = it.filter(Char::isDigit).take(5) },
                label = { Text("Port") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                modifier = Modifier.fillMaxWidth().focusRequester(primary).focusHighlight(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedButton(
                onClick = {
                    val port = portText.toIntOrNull()
                    portNote = if (port == null || port < 1024 || port > 65535) {
                        "Pick a port between 1024 and 65535."
                    } else {
                        store.set("port", port)
                        MmmRuntime.restartListener(context)
                        "Listening on port ${store.current.port}."
                    }
                },
                modifier = Modifier.fillMaxWidth().focusHighlight(),
            ) {
                Text("Apply port")
            }
            portNote?.let { Hint(it) }
            SettingSwitch(
                label = "Start on boot",
                checked = settings.startOnBoot,
                onChange = { store.set("startOnBoot", it) },
            )
        }

        SectionCard("Events") {
            NumberSetting(
                label = "Backlog events",
                value = settings.backlogEvents,
                onChange = { store.set("backlogEvents", it) },
            )
            NumberSetting(
                label = "Backlog hours",
                value = settings.backlogHours,
                onChange = { store.set("backlogHours", it) },
            )
            NumberSetting(
                label = "Proxy reply timeout (ms)",
                value = settings.proxyReplyTimeoutMs.toInt(),
                onChange = { store.set("proxyReplyTimeoutMs", it) },
            )
            NumberSetting(
                label = "Handle cap",
                value = settings.handleCap,
                onChange = { store.set("handleCap", it) },
            )
            Hint("Backlog limits apply to subscriptions created after the change.")
        }

        SectionCard("Theme") {
            ThemeMode.entries.forEach { mode ->
                Row(
                    modifier = Modifier.fillMaxWidth().focusHighlight(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RadioButton(
                        selected = settings.theme == mode,
                        onClick = { store.set("theme", mode.name) },
                    )
                    Text(
                        mode.name.lowercase().replaceFirstChar { it.uppercase() },
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }

        Hint("Every setting here is also readable and writable over RPC through SettingsBridge.")
    }
}

@Composable
private fun SettingSwitch(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Switch(
            checked = checked,
            onCheckedChange = onChange,
            modifier = Modifier.focusHighlight(),
        )
    }
}

@Composable
private fun NumberSetting(label: String, value: Int, onChange: (Int) -> Unit) {
    var text by remember(value) { mutableStateOf(value.toString()) }
    OutlinedTextField(
        value = text,
        onValueChange = { raw ->
            text = raw.filter(Char::isDigit).take(7)
            text.toIntOrNull()?.let(onChange)
        },
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth().focusHighlight(),
    )
    Spacer(Modifier.height(6.dp))
}
