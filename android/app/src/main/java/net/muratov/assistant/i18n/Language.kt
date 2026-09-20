package net.muratov.assistant.i18n

import android.content.Context
import android.content.res.Configuration
import android.content.res.Resources
import androidx.compose.foundation.layout.Box
import androidx.activity.ComponentActivity
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.material3.*
import org.json.JSONObject
import java.util.Locale

/** Local UI preference; never changes the language of server data or analysis. */
object Language {
    private var application: Context? = null
    private var catalog: JSONObject? = null
    val preference: String get() = application?.getSharedPreferences("language", Context.MODE_PRIVATE)?.getString("choice", "system") ?: "system"
    fun normalize(tag: String): String = when (tag.replace('_', '-').substringBefore('-').lowercase(Locale.ROOT)) {
        "ru" -> "ru"; "zh" -> "zh"; else -> "en"
    }
    val code: String get() = normalize(if (preference == "system") (if (application == null) Locale.getDefault().toLanguageTag() else Resources.getSystem().configuration.locales[0].toLanguageTag()) else preference)
    val locale: Locale get() = Locale.forLanguageTag(when (code) { "zh" -> "zh-CN"; "ru" -> "ru-RU"; else -> "en-US" })
    fun initialize(context: Context) {
        application = context.applicationContext
        catalog = JSONObject(context.assets.open("translations.json").bufferedReader().use { it.readText() })
    }
    fun set(context: Context, value: String) {
        if (value !in listOf("system", "ru", "en", "zh")) return
        context.getSharedPreferences("language", Context.MODE_PRIVATE).edit().putString("choice", value).apply()
    }
    fun wrap(context: Context): Context {
        val config = Configuration(context.resources.configuration)
        config.setLocale(locale)
        return context.createConfigurationContext(config)
    }
    fun text(source: String, args: Array<out Any?>): String {
        val template = if (catalog == null || code == "ru") source else catalog?.optJSONObject(source)?.optString(code, source) ?: source
        return Regex("\\{(\\d+)\\}").replace(template) { match ->
            val index = match.groupValues[1].toInt()
            if (index < args.size) args[index].toString() else match.value
        }
    }
}
fun tr(source: String, vararg args: Any?): String = Language.text(source, args)

open class LocalizedActivity : ComponentActivity() {
    private var attachedLanguage = ""
    override fun attachBaseContext(base: Context) {
        attachedLanguage = Language.code
        super.attachBaseContext(Language.wrap(base))
    }
    override fun onResume() {
        super.onResume()
        if (attachedLanguage != Language.code) recreate()
    }
}

@Composable fun LanguageSetting() {
    val context = LocalContext.current
    var expanded by remember { mutableStateOf(false) }
    val labels = linkedMapOf("system" to tr("Как в системе"), "ru" to "Русский", "en" to "English", "zh" to "简体中文")
    Box {
        TextButton(onClick = { expanded = true }) { Text(tr("Язык") + ": " + labels[Language.preference]) }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            labels.forEach { (code, label) ->
                DropdownMenuItem(text = { Text(label) }, onClick = {
                    expanded = false
                    Language.set(context, code)
                    var current: Context = context
                    while (current is android.content.ContextWrapper && current !is android.app.Activity) current = current.baseContext
                    (current as? android.app.Activity)?.recreate()
                })
            }
        }
    }
}

fun dateFormatter(): java.time.format.DateTimeFormatter = if (Language.code == "ru") java.time.format.DateTimeFormatter.ofPattern("dd.MM.yyyy", Language.locale) else java.time.format.DateTimeFormatter.ofLocalizedDate(java.time.format.FormatStyle.SHORT).withLocale(Language.locale)
fun dateTimeFormatter(): java.time.format.DateTimeFormatter = if (Language.code == "ru") java.time.format.DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm", Language.locale) else java.time.format.DateTimeFormatter.ofLocalizedDateTime(java.time.format.FormatStyle.SHORT).withLocale(Language.locale)
