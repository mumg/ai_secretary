package net.muratov.assistant.data

import android.content.Context
import net.muratov.assistant.setup.normalizeServerUrl

class SettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences("connection", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = preferences.getString("server_url", "")!!
        set(value) = preferences.edit().putString("server_url", value.trim()).apply()

    val isConfigured: Boolean
        get() = normalizeServerUrl(serverUrl) != null

    fun saveConnection(url: String, alias: String?) {
        require(normalizeServerUrl(url) != null)
        preferences.edit().putString("server_url", normalizeServerUrl(url))
            .putString("certificate_alias", alias).apply()
    }

    var certificateAlias: String?
        get() = preferences.getString("certificate_alias", null)
        set(value) = preferences.edit().putString("certificate_alias", value).apply()
}
