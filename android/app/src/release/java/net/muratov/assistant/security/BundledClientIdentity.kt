package net.muratov.assistant.security

import android.content.Context
import javax.net.ssl.KeyManager

/** Release builds only use identities selected from Android KeyChain. */
object BundledClientIdentity {
    fun keyManagers(@Suppress("UNUSED_PARAMETER") context: Context): Array<KeyManager>? = null
}
