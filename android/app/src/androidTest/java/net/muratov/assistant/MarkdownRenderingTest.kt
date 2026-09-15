package net.muratov.assistant

import android.text.Spanned
import android.widget.TextView
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.noties.markwon.Markwon
import io.noties.markwon.core.spans.StrongEmphasisSpan
import io.noties.markwon.core.spans.EmphasisSpan
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class MarkdownRenderingTest {
    @Test
    fun meetingSummaryRendersNativeEmphasisAndPreservesSourceLabels() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        instrumentation.runOnMainSync {
            val context = instrumentation.targetContext
            val view = TextView(context).apply { setTextIsSelectable(true) }
            Markwon.create(context).setMarkdown(view,
                "## Решения\n\n**Бюджет согласован** [E1].\n\n- *Уточнить срок*\n- Ответственный: тестовый участник")
            val rendered = view.text as Spanned
            assertFalse(rendered.toString().contains("**"))
            assertFalse(rendered.toString().contains("##"))
            assertTrue(rendered.toString().contains("[E1]"))
            assertTrue(rendered.getSpans(0, rendered.length, StrongEmphasisSpan::class.java).isNotEmpty())
            assertTrue(rendered.getSpans(0, rendered.length, EmphasisSpan::class.java).isNotEmpty())
            assertTrue(view.isTextSelectable)
        }
    }
}
