package net.muratov.assistant.security

import android.content.Context
import javax.net.ssl.KeyManager

/** Release builds contain no bundled credentials. App-private QR identities are loaded by ApiFactory. */
object BundledClientIdentity {
    fun keyManagers(@Suppress("UNUSED_PARAMETER") context: Context): Array<KeyManager>? = null
}
