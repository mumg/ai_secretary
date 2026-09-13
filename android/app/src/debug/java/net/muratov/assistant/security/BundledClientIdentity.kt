package net.muratov.assistant.security

import android.content.Context
import net.muratov.assistant.BuildConfig
import java.security.KeyStore
import javax.net.ssl.KeyManager
import javax.net.ssl.KeyManagerFactory

/** Debug-only mTLS identity. Never move this implementation into the main source set. */
object BundledClientIdentity {
    fun keyManagers(context: Context): Array<KeyManager> {
        val resourceName = BuildConfig.CLIENT_CERT_RESOURCE
        require(resourceName.isNotBlank()) {
            "Debug client certificate is not configured"
        }
        val resourceId = context.resources.getIdentifier(resourceName, "raw", context.packageName)
        require(resourceId != 0) {
            "Configured debug client certificate resource is missing"
        }
        val password = BuildConfig.CLIENT_CERT_PASSWORD.toCharArray()
        val keyStore = KeyStore.getInstance("PKCS12").apply {
            context.resources.openRawResource(resourceId).use {
                load(it, password)
            }
        }
        return KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).run {
            init(keyStore, password)
            keyManagers
        }
    }
}
