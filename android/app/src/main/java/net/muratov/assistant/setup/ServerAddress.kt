package net.muratov.assistant.setup

import java.net.URI

/** The API and websocket endpoints are rooted at this HTTPS origin. */
fun normalizeServerUrl(value: String): String? = runCatching {
    val uri = URI(value.trim())
    require(uri.scheme.equals("https", ignoreCase = true))
    require(!uri.host.isNullOrBlank() && uri.rawUserInfo == null)
    require(uri.rawQuery == null && uri.rawFragment == null)
    require(uri.rawPath.isNullOrEmpty() || uri.rawPath == "/")
    require(uri.port == -1 || uri.port in 1..65535)
    "https://${uri.rawAuthority}".trimEnd('/')
}.getOrNull()
