package net.muratov.assistant.ui

import android.graphics.Color
import android.util.TypedValue
import android.widget.TextView
import android.text.method.LinkMovementMethod
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import io.noties.markwon.Markwon

@Composable
fun MarkdownText(value: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val renderer = remember(context) { Markwon.create(context) }
    val textColor = MaterialTheme.colorScheme.onSurface.toArgb()
    val linkColor = MaterialTheme.colorScheme.primary.toArgb()
    AndroidView(
        modifier = modifier,
        factory = { TextView(it).apply {
            setBackgroundColor(Color.TRANSPARENT)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
            setLineSpacing(0f, 1.18f)
            setTextIsSelectable(true)
            movementMethod = LinkMovementMethod.getInstance()
        } },
        update = { view ->
            view.setTextColor(textColor)
            view.setLinkTextColor(linkColor)
            renderer.setMarkdown(view, value)
        },
    )
}
