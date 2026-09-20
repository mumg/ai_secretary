package net.muratov.assistant

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import net.muratov.assistant.i18n.Language
import net.muratov.assistant.i18n.tr
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class LanguageTest {
    @Test fun selectionPersistsAndDoesNotTranslateUserContent() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val previous = Language.preference
        try {
            for ((language, title) in listOf("ru" to "Поручения", "en" to "Delegations", "zh" to "委派任务")) {
                Language.set(context, language)
                assertEquals(language, Language.preference)
                assertEquals(title, tr("Поручения"))
                assertEquals(language, Language.wrap(context).resources.configuration.locales[0].language)
                val question = tr("Вопрос: {0}", "Настройки {1}")
                assertTrue(question.endsWith("Настройки {1}"))
                Language.initialize(context)
                assertEquals(language, Language.code)
            }
            assertEquals("en", Language.normalize("de-DE"))
            assertEquals("zh", Language.normalize("zh-Hant-TW"))
            Language.set(context,"system")
            assertEquals(Language.normalize(android.content.res.Resources.getSystem().configuration.locales[0].toLanguageTag()),Language.code)
        } finally { Language.set(context, previous) }
    }
}
