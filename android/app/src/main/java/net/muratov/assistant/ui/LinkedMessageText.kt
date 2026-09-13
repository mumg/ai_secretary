package net.muratov.assistant.ui

import android.graphics.Color
import android.text.Spannable
import android.text.SpannableStringBuilder
import android.text.method.LinkMovementMethod
import android.text.style.URLSpan
import android.util.Patterns
import android.util.TypedValue
import android.widget.TextView
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.material3.MaterialTheme

@Composable
fun LinkedMessageText(value: String, modifier: Modifier = Modifier) {
    val textColor = MaterialTheme.colorScheme.onSurface.toArgb()
    val linkColor = MaterialTheme.colorScheme.primary.toArgb()
    AndroidView(
        modifier = modifier,
        factory = { context ->
            TextView(context).apply {
                setBackgroundColor(Color.TRANSPARENT)
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f)
                setLineSpacing(0f, 1.18f)
                movementMethod = LinkMovementMethod.getInstance()
                linksClickable = true
                setTextIsSelectable(true)
            }
        },
        update = { view ->
            view.setTextColor(textColor)
            view.setLinkTextColor(linkColor)
            view.text = linkedText(value)
        },
    )
}

private fun linkedText(value: String): SpannableStringBuilder {
    val builder = SpannableStringBuilder()
    parseMessageLinks(normalizeMessageBody(value)).forEach { part ->
        val start = builder.length
        builder.append(part.text)
        part.url?.let {
            builder.setSpan(
                URLSpan(it),
                start,
                builder.length,
                Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
            )
        }
    }

    val matcher = Patterns.WEB_URL.matcher(builder)
    while (matcher.find()) {
        if (builder.getSpans(matcher.start(), matcher.end(), URLSpan::class.java).isNotEmpty()) {
            continue
        }
        val matched = matcher.group()
        val url = if (matched.startsWith("http://") || matched.startsWith("https://")) {
            matched
        } else {
            "https://$matched"
        }
        builder.setSpan(
            URLSpan(url),
            matcher.start(),
            matcher.end(),
            Spannable.SPAN_EXCLUSIVE_EXCLUSIVE,
        )
    }
    return builder
}
