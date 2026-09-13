package net.muratov.assistant.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val ImproverDarkColors = darkColorScheme(
    primary = Color(0xFF85D6A7),
    onPrimary = Color(0xFF06391F),
    primaryContainer = Color(0xFF175231),
    onPrimaryContainer = Color(0xFFB1F3C8),
    secondary = Color(0xFFA9C7FF),
    onSecondary = Color(0xFF0C315F),
    secondaryContainer = Color(0xFF253E5D),
    onSecondaryContainer = Color(0xFFD5E3FF),
    background = Color(0xFF090D12),
    onBackground = Color(0xFFE2E7ED),
    surface = Color(0xFF111820),
    onSurface = Color(0xFFE2E7ED),
    surfaceVariant = Color(0xFF1A222C),
    onSurfaceVariant = Color(0xFFBEC7D1),
    outline = Color(0xFF65717D),
    error = Color(0xFFFF8A84),
    onError = Color(0xFF690005),
    errorContainer = Color(0xFF5B1A1D),
    onErrorContainer = Color(0xFFFFDAD7),
)

@Composable
fun ImproverTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = ImproverDarkColors,
        content = content,
    )
}
