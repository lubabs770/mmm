package lubabs770.mmm.modemizer.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import lubabs770.mmm.modemizer.auth.AuditLog
import lubabs770.mmm.modemizer.service.MmmRuntime

@Composable
fun ActivityScreen(primary: FocusRequester) {
    val context = LocalContext.current
    val audit = remember { MmmRuntime.audit(context) }
    val tokens = remember { MmmRuntime.tokens(context) }
    val entries by audit.entries.collectAsStateWithLifecycle()
    val counts by audit.callCounts.collectAsStateWithLifecycle()
    val clients by tokens.clients.collectAsStateWithLifecycle()

    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        SectionCard("Calls per client") {
            if (counts.isEmpty()) {
                Hint("No calls counted yet.")
            } else {
                counts.forEach { (id, count) ->
                    val label = clients.firstOrNull { it.id == id }?.label ?: id
                    InfoRow(label, count.toString())
                }
            }
            OutlinedButton(
                onClick = { audit.clear() },
                modifier = Modifier.fillMaxWidth().focusRequester(primary).focusHighlight(),
            ) {
                Text("Clear log")
            }
        }

        SectionCard("Log") {
            if (entries.isEmpty()) {
                Hint("Nothing recorded yet.")
            }
            entries.asReversed().forEach { entry ->
                Text(
                    "${formatTime(entry.ts)}  ${entry.kind.label()}  ${entry.detail}",
                    style = MaterialTheme.typography.bodySmall,
                    color = if (entry.kind == AuditLog.Kind.AUTH_FAIL ||
                        entry.kind == AuditLog.Kind.REJECT ||
                        entry.kind == AuditLog.Kind.ERROR
                    ) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.onSurface
                    },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

private fun AuditLog.Kind.label(): String = name.lowercase().replace('_', '-')
