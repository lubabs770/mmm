package lubabs770.mmm.modemizer.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import lubabs770.mmm.modemizer.settings.ThemeMode

/** Material You where the platform has it, a fixed palette everywhere else. */
private val LightColors = lightColorScheme(
    primary = Color(0xFF00677F),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFB4EAFF),
    onPrimaryContainer = Color(0xFF001F28),
    secondary = Color(0xFF4C616B),
    tertiary = Color(0xFF585B7E),
    error = Color(0xFFBA1A1A),
    background = Color(0xFFFBFCFE),
    surface = Color(0xFFFBFCFE),
    surfaceVariant = Color(0xFFDBE4E8),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF59D4F1),
    onPrimary = Color(0xFF003544),
    primaryContainer = Color(0xFF004D60),
    onPrimaryContainer = Color(0xFFB4EAFF),
    secondary = Color(0xFFB3CAD5),
    tertiary = Color(0xFFC0C3EB),
    error = Color(0xFFFFB4AB),
    background = Color(0xFF191C1E),
    surface = Color(0xFF191C1E),
    surfaceVariant = Color(0xFF3F484B),
)

@Composable
fun MmmTheme(mode: ThemeMode, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
    }
    val context = LocalContext.current
    val colors = when {
        Build.VERSION.SDK_INT >= 31 ->
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> DarkColors
        else -> LightColors
    }
    MaterialTheme(colorScheme = colors, content = content)
}
