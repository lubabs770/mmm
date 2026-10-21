package lubabs770.mmm.modemizer.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import lubabs770.mmm.modemizer.service.MmmRuntime

@Composable
fun SubscriptionsScreen(primary: FocusRequester) {
    val context = LocalContext.current
    val events = remember { MmmRuntime.events(context) }
    val subscriptions by events.state.collectAsStateWithLifecycle()

    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        if (subscriptions.isEmpty()) {
            Hint("No subscriptions. A client creates one with `subscribe`.")
        }
        subscriptions.forEachIndexed { index, sub ->
            SectionCard(sub.name) {
                InfoRow("Client", sub.tokenId)
                InfoRow("Actions", sub.actions.joinToString(", ") { it.substringAfterLast('.') })
                InfoRow("Durable", if (sub.durable) "yes" else "no")
                InfoRow("Attached", if (sub.attached) "live" else "buffering")
                InfoRow("Delivered", sub.delivered.toString())
                InfoRow("Backlog", "${sub.backlogSize} waiting, ${sub.dropped} dropped")
                if (sub.durable) {
                    TextButton(
                        onClick = { events.clearBacklog(sub.tokenId, sub.name) },
                        modifier = Modifier
                            .focusHighlight()
                            .then(if (index == 0) Modifier.focusRequester(primary) else Modifier),
                    ) {
                        Text("Clear backlog")
                    }
                }
            }
        }
    }
}
