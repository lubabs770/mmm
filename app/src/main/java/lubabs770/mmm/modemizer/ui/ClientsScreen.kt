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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.auth.Scope
import lubabs770.mmm.modemizer.service.MmmRuntime

@Composable
fun ClientsScreen(primary: FocusRequester) {
    val context = LocalContext.current
    val tokens = remember { MmmRuntime.tokens(context) }
    val audit = remember { MmmRuntime.audit(context) }
    val clients by tokens.clients.collectAsStateWithLifecycle()

    var pairing by remember { mutableStateOf(false) }
    var created by remember { mutableStateOf<Pair<String, String>?>(null) }

    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        Button(
            onClick = { pairing = true },
            modifier = Modifier.fillMaxWidth().focusRequester(primary).focusHighlight(),
        ) {
            Text("Pair new client")
        }
        Spacer(Modifier.height(8.dp))

        if (clients.isEmpty()) {
            Hint("No clients yet. Pair one to get a token.")
        }
        clients.forEach { client ->
            SectionCard(client.label) {
                InfoRow("Id", client.id)
                InfoRow("Scope", client.scope.describe())
                InfoRow("Created", formatTime(client.createdAt))
                InfoRow("Last seen", formatAge(client.lastSeen))
                Spacer(Modifier.height(6.dp))
                TextButton(
                    onClick = { MmmRuntime.revokeClient(context, client.id) },
                    modifier = Modifier.focusHighlight(),
                ) {
                    Text("Revoke", color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }

    if (pairing) {
        PairDialog(
            onDismiss = { pairing = false },
            onCreate = { label, scope ->
                val (client, token) = tokens.create(label, scope)
                audit.log(AuditLog.Kind.PAIR, "paired ${client.label} (${scope.describe()})", client.id)
                created = client.label to token
                pairing = false
            },
        )
    }

    created?.let { (label, token) ->
        TokenDialog(label = label, token = token, onDismiss = { created = null })
    }
}

@Composable
private fun PairDialog(onDismiss: () -> Unit, onCreate: (String, Scope) -> Unit) {
    var label by remember { mutableStateOf("") }
    var full by remember { mutableStateOf(true) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Pair a client") },
        text = {
            Column {
                OutlinedTextField(
                    value = label,
                    onValueChange = { label = it },
                    label = { Text("Label") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth().focusHighlight(),
                )
                Spacer(Modifier.height(12.dp))
                ScopeChoice(
                    selected = full,
                    onSelect = { full = it },
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onCreate(label, if (full) Scope.FULL else Scope.mcpPreset()) },
                modifier = Modifier.focusHighlight(),
            ) { Text("Create token") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, modifier = Modifier.focusHighlight()) { Text("Cancel") }
        },
    )
}

@Composable
private fun ScopeChoice(selected: Boolean, onSelect: (Boolean) -> Unit) {
    Column {
        listOf(
            true to "Full — all of Android",
            false to "Assistant — SMS, contacts, status, notify",
        ).forEach { (value, text) ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .focusHighlight(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(selected = selected == value, onClick = { onSelect(value) })
                Text(text, style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
private fun TokenDialog(label: String, token: String, onDismiss: () -> Unit) {
    val clipboard = LocalClipboardManager.current
    val termux = "mmm pair $token"
    val ssh = "mmm pair $token --ssh <phone-host>"
    val adb = "mmm pair $token --adb"

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Token for $label") },
        text = {
            Column(modifier = Modifier.verticalScroll(rememberScrollState())) {
                Text(
                    "Copy it now — only its hash is kept on the phone.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
                Spacer(Modifier.height(8.dp))
                Text(token, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(12.dp))
                CopyRow("In Termux", termux, clipboard::setText)
                CopyRow("Over SSH", ssh, clipboard::setText)
                CopyRow("Over adb", adb, clipboard::setText)
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    clipboard.setText(AnnotatedString(token))
                    onDismiss()
                },
                modifier = Modifier.focusHighlight(),
            ) { Text("Copy token and close") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, modifier = Modifier.focusHighlight()) { Text("Close") }
        },
    )
}

@Composable
private fun CopyRow(title: String, command: String, copy: (AnnotatedString) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Text(title, style = MaterialTheme.typography.labelMedium)
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                command,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.fillMaxWidth(0.72f),
            )
            TextButton(
                onClick = { copy(AnnotatedString(command)) },
                modifier = Modifier.focusHighlight(),
            ) { Text("Copy") }
        }
        Spacer(Modifier.height(6.dp))
    }
}
