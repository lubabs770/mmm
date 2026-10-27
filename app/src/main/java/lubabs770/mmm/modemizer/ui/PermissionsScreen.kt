package lubabs770.mmm.modemizer.ui

import android.content.pm.PackageManager
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import lubabs770.mmm.modemizer.service.Privileged
import lubabs770.mmm.modemizer.service.Root

@Composable
fun PermissionsScreen(primary: FocusRequester) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var refresh by remember { mutableIntStateOf(0) }
    var rootResult by remember { mutableStateOf<String?>(null) }

    val declared = remember(refresh) {
        val info = context.packageManager.getPackageInfo(
            context.packageName,
            PackageManager.GET_PERMISSIONS,
        )
        (info.requestedPermissions ?: emptyArray()).map { name ->
            name to Privileged.holds(context, name)
        }.sortedBy { it.second }
    }
    val missing = declared.filterNot { it.second }.map { it.first }

    val request = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { refresh++ }

    Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        SectionCard("Grant") {
            Button(
                onClick = { if (missing.isNotEmpty()) request.launch(missing.toTypedArray()) },
                enabled = missing.isNotEmpty(),
                modifier = Modifier.fillMaxWidth().focusRequester(primary).focusHighlight(),
            ) {
                Text(if (missing.isEmpty()) "Everything is granted" else "Ask for ${missing.size} missing")
            }
            Spacer(Modifier.height(8.dp))
            OutlinedButton(
                onClick = {
                    scope.launch {
                        rootResult = withContext(Dispatchers.IO) { grantWithRoot(context.packageName, missing) }
                        refresh++
                    }
                },
                enabled = Root.installed && missing.isNotEmpty(),
                modifier = Modifier.fillMaxWidth().focusHighlight(),
            ) {
                Text(if (Root.installed) "Grant missing (root)" else "Grant missing (no root)")
            }
            rootResult?.let { Hint(it) }
            Hint(
                if (Privileged.isPrivApp(context)) {
                    "Installed as a priv-app: privileged permissions can be held."
                } else {
                    "Not a priv-app. MODIFY_PHONE_STATE and READ_PRIVILEGED_PHONE_STATE " +
                        "stay denied until the Magisk module is installed; everything else works."
                }
            )
        }

        SectionCard("Declared permissions") {
            declared.forEach { (name, granted) ->
                InfoRow(name.removePrefix("android.permission."), if (granted) "granted" else "denied")
            }
        }
    }
}

/**
 * `pm grant` only works for runtime permissions, so some of these will fail on
 * purpose — install-time and privileged ones are decided by where the APK lives.
 */
private fun grantWithRoot(packageName: String, missing: List<String>): String {
    if (missing.isEmpty()) return "Nothing missing."
    var granted = 0
    val failed = ArrayList<String>()
    missing.forEach { permission ->
        // WRITE_SETTINGS is an appop: pm grant always refuses it.
        val result = if (permission == android.Manifest.permission.WRITE_SETTINGS) {
            Root.run("appops set $packageName WRITE_SETTINGS allow")
        } else {
            Root.run("pm grant $packageName $permission")
        }
        if (result.ok) granted++ else failed += permission.removePrefix("android.permission.")
    }
    return buildString {
        append("Granted $granted of ${missing.size}.")
        if (failed.isNotEmpty()) append(" Not grantable this way: ${failed.joinToString(", ")}.")
    }
}
