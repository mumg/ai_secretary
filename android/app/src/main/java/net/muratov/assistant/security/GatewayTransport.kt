package net.muratov.assistant.security

import okhttp3.*
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.IOException
import java.net.*
import java.security.KeyStore
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import javax.net.SocketFactory
import javax.net.ssl.*
import kotlin.concurrent.thread

/** A transient loopback socket pair gives Android TLS real file descriptors.
 * Only encrypted inner TLS bytes cross this local bridge into the WSS tunnel.
 */
object GatewayTransport {
    private val tls13 = ConnectionSpec.Builder(ConnectionSpec.MODERN_TLS).tlsVersions(TlsVersion.TLS_1_3).build()
    fun externalClient(identity: GatewayIdentity.Identity): OkHttpClient {
        val trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(null as KeyStore?) }.trustManagers.filterIsInstance<X509TrustManager>().single()
        val ssl = SSLContext.getInstance("TLS").apply { init(GatewayIdentity.keyManagers(identity.cert, identity.key), arrayOf(trust), null) }
        return OkHttpClient.Builder().sslSocketFactory(ssl.socketFactory, trust)
            .connectionSpecs(listOf(tls13)).protocols(listOf(Protocol.HTTP_1_1))
            .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
            .connectTimeout(25, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).writeTimeout(30, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS).build()
    }
    fun client(identity: GatewayIdentity.Identity): OkHttpClient = client(identity, externalClient(identity))

    internal fun client(identity: GatewayIdentity.Identity, outer: OkHttpClient, multiplex: Boolean = true): OkHttpClient {
        val trust = GatewayIdentity.trustServer(identity)
        val ssl = SSLContext.getInstance("TLS").apply { init(GatewayIdentity.keyManagers(identity.innerClient, identity.innerKey), arrayOf(trust), null) }
        val factory = object : SocketFactory() {
            override fun createSocket(): Socket = if (!multiplex) TunnelSocket(identity.gateway, outer) else GatewayMultiplexer.socket(identity) { timeout ->
                TunnelSocket(identity.gateway, outer).apply { connect(InetSocketAddress(URI(identity.gateway).host, 443), timeout) }
            }
            override fun createSocket(host: String, port: Int): Socket = createSocket().apply { connect(InetSocketAddress(host, port), 25000) }
            override fun createSocket(host: InetAddress, port: Int): Socket = createSocket(host.hostAddress!!, port)
            override fun createSocket(host: String, port: Int, local: InetAddress, localPort: Int): Socket = createSocket(host, port)
            override fun createSocket(host: InetAddress, port: Int, local: InetAddress, localPort: Int): Socket = createSocket(host, port)
        }
        return OkHttpClient.Builder().proxy(Proxy.NO_PROXY).socketFactory(factory)
            .sslSocketFactory(ssl.socketFactory, trust)
            .hostnameVerifier { host, session -> host == URI(identity.gateway).host &&
                session.peerCertificates.firstOrNull()?.let { GatewayIdentity.matchesInnerServer(identity, it) } == true }
            .connectionSpecs(listOf(tls13)).protocols(listOf(Protocol.HTTP_1_1))
            .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
            .connectTimeout(30, TimeUnit.SECONDS).writeTimeout(30, TimeUnit.SECONDS).readTimeout(6, TimeUnit.MINUTES)
            .addInterceptor { chain ->
                val request = chain.request()
                val origin = URI(identity.gateway)
                require(request.url.host == origin.host && request.url.port == (if (origin.port == -1) 443 else origin.port)) { "Unexpected gateway destination" }
                chain.proceed(request)
            }.build()
    }
    private class TunnelSocket(private val gateway: String, private val client: OkHttpClient) : Socket() {
        @Volatile private var bridge: Socket? = null
        @Volatile private var websocket: WebSocket? = null
        override fun connect(endpoint: SocketAddress) = connect(endpoint, 25000)
        override fun connect(endpoint: SocketAddress, timeout: Int) {
            val opened = CountDownLatch(1)
            var failure: IOException? = null
            val local = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
            try {
                local.soTimeout = if (timeout > 0) timeout else 25000
                super.connect(InetSocketAddress("127.0.0.1", local.localPort), local.soTimeout)
                bridge = local.accept().apply { tcpNoDelay = true }
            } finally { local.close() }
            try {
                websocket = client.newWebSocket(Request.Builder().url("$gateway/api/v1/tunnels/client")
                    .header("Sec-WebSocket-Protocol", "ai-secretary-tunnel.v1").build(), object : WebSocketListener() {
                    override fun onOpen(webSocket: WebSocket, response: Response) {
                        if (response.header("Sec-WebSocket-Protocol") != "ai-secretary-tunnel.v1") {
                            failure = IOException("Некорректный протокол гейтвея"); webSocket.cancel()
                        }
                        opened.countDown()
                    }
                    override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                        if (bytes.size > (1 shl 20)) { webSocket.cancel(); close(); return }
                        try { bridge?.getOutputStream()?.write(bytes.toByteArray()) }
                        catch (_: IOException) { webSocket.cancel(); close() }
                    }
                    override fun onMessage(webSocket: WebSocket, text: String) { webSocket.cancel(); close() }
                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        val message = when (response?.code) {
                            429 -> "Достигнут лимит соединений со шлюзом (HTTP 429)"
                            503 -> "Сервер не подключён к шлюзу (HTTP 503)"
                            null -> "Соединение со шлюзом прервано"
                            else -> "Шлюз отклонил подключение (HTTP ${response.code})"
                        }
                        failure = IOException(message, t)
                        opened.countDown(); finishTransport()
                    }
                    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { webSocket.close(code, null); finishTransport() }
                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { finishTransport() }
                })
                if (!opened.await((if (timeout > 0) timeout else 25000).toLong(), TimeUnit.MILLISECONDS)) throw IOException("Гейтвей не ответил вовремя")
                failure?.let { throw it }
                if (isClosed) throw IOException("Соединение закрыто")
                thread(name = "gateway-tls-bridge", isDaemon = true) {
                    try {
                        val input = bridge!!.getInputStream()
                        val buffer = ByteArray(16384)
                        while (!isClosed) {
                            val n = input.read(buffer); if (n < 0) break
                            val ws = websocket ?: break
                            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30)
                            while (ws.queueSize() > (1 shl 20) && !isClosed) {
                                if (System.nanoTime() > deadline) throw IOException("Gateway write timeout")
                                Thread.sleep(10)
                            }
                            if (!ws.send(buffer.toByteString(0, n))) break
                        }
                    } catch (_: Exception) { /* Closing propagates the failure to OkHttp. */ }
                    finally { finishTransport() }
                }
            } catch (error: Exception) { close(); throw IOException("Не удалось открыть туннель: ${error.message}", error) }
        }
        private fun finishTransport() {
            // Let OkHttp drain bytes already received before observing EOF.
            runCatching { bridge?.close() }
        }
        override fun close() {
            websocket?.cancel()
            runCatching { bridge?.close() }
            super.close()
        }
    }
}
