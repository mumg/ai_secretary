package net.muratov.assistant.ui

import java.net.URI

data class MessageTextPart(
    val text: String,
    val url: String? = null,
)

fun normalizeMessageBody(value: String): String {
    val invisibleCharacters = Regex("[\u034F\u061C\u200B-\u200F\u2060\uFEFF]")
    val normalizedLines = value
        .replace('\u00A0', ' ')
        .replace('\u2800', ' ')
        .replace(invisibleCharacters, "")
        .lines()
        .map { it.trimEnd() }
    return normalizedLines.joinToString("\n").replace(Regex("\n[ \t\r]*\n(?:[ \t\r]*\n)+"), "\n\n")
        .trim()
}

fun parseMessageLinks(value: String): List<MessageTextPart> {
    val parts = mutableListOf<MessageTextPart>()
    var plainStart = 0
    var position = 0
    while (position < value.length) {
        val labelStart = value.indexOf('[', position)
        if (labelStart < 0) break
        val labelEnd = value.indexOf("](", labelStart + 1)
        if (labelEnd < 0) break

        var cursor = labelEnd + 2
        var nestedParentheses = 0
        var urlEnd = -1
        while (cursor < value.length) {
            when (value[cursor]) {
                '(' -> nestedParentheses++
                ')' -> if (nestedParentheses == 0) {
                    urlEnd = cursor
                    break
                } else {
                    nestedParentheses--
                }
            }
            cursor++
        }
        if (urlEnd < 0) break

        val label = value.substring(labelStart + 1, labelEnd).trim()
        val url = value.substring(labelEnd + 2, urlEnd).trim()
        if (label.isEmpty() || !(url.startsWith("https://") || url.startsWith("http://"))) {
            position = labelStart + 1
            continue
        }
        if (labelStart > plainStart) {
            parts += MessageTextPart(value.substring(plainStart, labelStart))
        }
        parts += MessageTextPart(label, url)
        position = urlEnd + 1
        plainStart = position
    }
    if (plainStart < value.length) parts += MessageTextPart(value.substring(plainStart))
    return parts.ifEmpty { listOf(MessageTextPart(value)) }.flatMap { part ->
        if (part.url == null) compactBareLinks(part.text) else listOf(part)
    }
}

private fun compactBareLinks(value: String): List<MessageTextPart> {
    val parts = mutableListOf<MessageTextPart>()
    var cursor = 0
    while (cursor < value.length) {
        val http = value.indexOf("http://", cursor, ignoreCase = true)
        val https = value.indexOf("https://", cursor, ignoreCase = true)
        val urlStart = listOf(http, https).filter { it >= 0 }.minOrNull() ?: break
        var urlEnd = urlStart
        var parentheses = 0
        while (urlEnd < value.length) {
            val character = value[urlEnd]
            if (character.isWhitespace() || character in charArrayOf('<', '>', '"', '[', ']')) break
            if (character == '(') parentheses++
            if (character == ')') {
                if (parentheses == 0) break
                parentheses--
            }
            urlEnd++
        }
        while (urlEnd > urlStart && value[urlEnd - 1] in charArrayOf('.', ',', ';', ':', '!', '?')) {
            urlEnd--
        }
        val url = value.substring(urlStart, urlEnd)

        val wrappedInParentheses = urlStart > 0 && value[urlStart - 1] == '(' &&
            urlEnd < value.length && value[urlEnd] == ')'
        if (wrappedInParentheses) {
            val lineStart = value.lastIndexOf('\n', urlStart - 2) + 1
            val labelSource = value.substring(lineStart, urlStart - 1)
            val label = labelSource.trim()
            if (label.isNotEmpty() && label.length <= 160 && lineStart >= cursor) {
                if (lineStart > cursor) parts += MessageTextPart(value.substring(cursor, lineStart))
                val indentation = labelSource.takeWhile(Char::isWhitespace)
                if (indentation.isNotEmpty()) parts += MessageTextPart(indentation)
                parts += MessageTextPart(label, url)
                cursor = urlEnd + 1
                continue
            }
        }

        if (urlStart > cursor) parts += MessageTextPart(value.substring(cursor, urlStart))
        val display = runCatching { URI(url).host }
            .getOrNull()
            ?.removePrefix("www.")
            ?.takeIf(String::isNotBlank)
            ?: "Открыть ссылку"
        parts += MessageTextPart(display, url)
        cursor = urlEnd
    }
    if (cursor < value.length) parts += MessageTextPart(value.substring(cursor))
    return parts.ifEmpty { listOf(MessageTextPart(value)) }
}
