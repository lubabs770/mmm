package lubabs770.mmm.modemizer.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.dp

/**
 * D-pad affordances.
 *
 * MMM is meant to run on keypad phones as well as touch phones, so focus has to be
 * visible on every interactive element — a flip phone has no cursor to tell you where
 * you are.
 */
@Composable
fun Modifier.focusHighlight(shape: Shape = RoundedCornerShape(12.dp)): Modifier {
    var focused by remember { mutableStateOf(false) }
    val color = if (focused) MaterialTheme.colorScheme.primary else Color.Transparent
    return this
        .onFocusChanged { focused = it.isFocused }
        .border(2.dp, color, shape)
}

/** Compact phones get a tighter gutter than the 16dp a touch phone wants. */
fun screenPadding(compact: Boolean): PaddingValues =
    if (compact) PaddingValues(horizontal = 8.dp, vertical = 8.dp)
    else PaddingValues(horizontal = 16.dp, vertical = 12.dp)
