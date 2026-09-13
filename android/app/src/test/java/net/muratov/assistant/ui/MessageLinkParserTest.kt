package net.muratov.assistant.ui

import org.junit.Assert.assertEquals
import org.junit.Test

class MessageLinkParserTest {
    @Test
    fun replacesMarkdownUrlWithLinkedLabel() {
        val parts = parseMessageLinks("Откройте [заказ](https://example.test/order/42) сегодня")

        assertEquals(
            listOf(
                MessageTextPart("Откройте "),
                MessageTextPart("заказ", "https://example.test/order/42"),
                MessageTextPart(" сегодня"),
            ),
            parts,
        )
    }

    @Test
    fun keepsParenthesesInsideUrl() {
        val parts = parseMessageLinks("[документ](https://example.test/a_(b)?x=1)")

        assertEquals(
            MessageTextPart("документ", "https://example.test/a_(b)?x=1"),
            parts.single(),
        )
    }

    @Test
    fun leavesUnsafeSchemeAsPlainText() {
        val value = "[запустить](javascript:alert(1))"

        assertEquals(listOf(MessageTextPart(value)), parseMessageLinks(value))
    }

    @Test
    fun removesInvisibleEmailSpacingArtifacts() {
        val value = "Начало\n \u200c \u200c \n\u2800\n\n\nПродолжение"

        assertEquals("Начало\n\nПродолжение", normalizeMessageBody(value))
    }

    @Test
    fun hidesParenthesizedUrlBehindLineLabel() {
        val parts = parseMessageLinks(
            "Текст письма\n\nView in browser (https://email.example.test/very/long/link)\nКонец"
        )

        assertEquals(
            listOf(
                MessageTextPart("Текст письма\n\n"),
                MessageTextPart("View in browser", "https://email.example.test/very/long/link"),
                MessageTextPart("\nКонец"),
            ),
            parts,
        )
    }

    @Test
    fun displaysBareUrlAsDomain() {
        val parts = parseMessageLinks("Сайт: https://www.example.test/very/long/link")

        assertEquals(
            listOf(
                MessageTextPart("Сайт: "),
                MessageTextPart("example.test", "https://www.example.test/very/long/link"),
            ),
            parts,
        )
    }
}
