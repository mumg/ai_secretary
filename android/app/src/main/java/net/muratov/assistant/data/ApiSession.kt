package net.muratov.assistant.data

import net.muratov.assistant.data.remote.ImproverApi
import okhttp3.OkHttpClient
import java.io.Closeable

/** Owns the repository's connection pool, independently of realtime and setup checks. */
internal class ApiSession(
    private val clientFactory: (String, String?) -> OkHttpClient,
) : Closeable {
    private data class Connection(
        val url: String,
        val alias: String?,
        val client: OkHttpClient,
        val api: ImproverApi,
    )

    private var connection: Connection? = null

    @Synchronized
    fun get(baseUrl: String, certificateAlias: String?): ImproverApi {
        val url = baseUrl.trim().trimEnd('/')
        connection?.let {
            if (it.url == url && it.alias == certificateAlias) return it.api
        }
        close()
        val client = clientFactory(url, certificateAlias)
        return ApiFactory.create(client, url).also {
            connection = Connection(url, certificateAlias, client, it)
        }
    }

    @Synchronized
    override fun close() {
        connection?.client?.let {
            it.dispatcher.cancelAll()
            it.connectionPool.evictAll()
            it.dispatcher.executorService.shutdown()
        }
        connection = null
    }
}
