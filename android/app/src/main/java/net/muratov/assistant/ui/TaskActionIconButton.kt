package net.muratov.assistant.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

private val taskActionShape = RoundedCornerShape(12.dp)

@Composable
fun TaskActionIconButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    highlighted: Boolean = false,
    content: @Composable () -> Unit,
) {
    FilledTonalIconButton(
        onClick = onClick,
        enabled = enabled,
        shape = taskActionShape,
        modifier = modifier
            .size(48.dp)
            .border(
                width = if (highlighted) 2.dp else 1.dp,
                color = if (highlighted) Color(0xFFFFD166) else MaterialTheme.colorScheme.outline,
                shape = taskActionShape,
            ),
        content = content,
    )
}
