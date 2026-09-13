package net.muratov.assistant.data

import android.content.Context
import net.muratov.assistant.BuildConfig

class SettingsStore(context: Context) {
    private val preferences = context.getSharedPreferences("connection", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = preferences.getString("server_url", BuildConfig.DEFAULT_SERVER_URL)!!
        set(value) = preferences.edit().putString("server_url", value.trim()).apply()

    var certificateAlias: String?
        get() = preferences.getString("certificate_alias", null)
        set(value) = preferences.edit().putString("certificate_alias", value).apply()
}
