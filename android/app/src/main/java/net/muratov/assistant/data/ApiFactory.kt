package net.muratov.assistant.data

import android.content.Context
import com.google.gson.GsonBuilder
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import net.muratov.assistant.data.remote.ImproverApi
import net.muratov.assistant.security.AppClientIdentity
import net.muratov.assistant.security.AliasKeyManager
import net.muratov.assistant.security.BundledClientIdentity
import java.security.KeyStore
import javax.net.ssl.KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager
import java.util.concurrent.TimeUnit

object ApiFactory {
    fun client(context: Context, baseUrl: String, certificateAlias: String?): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .readTimeout(6, TimeUnit.MINUTES)
            .apply {
                if (baseUrl.trim().startsWith("https://", ignoreCase = true)) {
                    val trustManagerFactory = TrustManagerFactory.getInstance(
                        TrustManagerFactory.getDefaultAlgorithm(),
                    ).apply { init(null as KeyStore?) }
                    val trustManager = trustManagerFactory.trustManagers
                        .filterIsInstance<X509TrustManager>()
                        .single()
                    val sslContext = SSLContext.getInstance("TLS")
                    val keyManagers: Array<KeyManager>? = certificateAlias?.let {
                        if (AppClientIdentity.isAppAlias(it)) {
                            followRedirects(false)
                            AppClientIdentity.keyManagers(context, baseUrl, it)
                        } else arrayOf(AliasKeyManager(context, it))
                    } ?: BundledClientIdentity.keyManagers(context)
                    sslContext.init(keyManagers, arrayOf(trustManager), null)
                    sslSocketFactory(sslContext.socketFactory, trustManager)
                }
            }
            .build()

    fun create(context: Context, baseUrl: String, certificateAlias: String?): ImproverApi {
        val client = client(context, baseUrl, certificateAlias)
        val gson = GsonBuilder().create()
        return Retrofit.Builder()
            .baseUrl(baseUrl.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(GsonConverterFactory.create(gson))
            .build()
            .create(ImproverApi::class.java)
    }
}
