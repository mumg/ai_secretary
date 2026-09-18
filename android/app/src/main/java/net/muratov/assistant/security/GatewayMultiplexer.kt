package net.muratov.assistant.security

import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketAddress
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** One outer WSS per identity; each yamux stream carries its own end-to-end TLS.
 * Wire format: https://github.com/hashicorp/yamux/blob/master/spec.md
 * Only client-initiated streams are supported; buffers and flow-control are bounded.
 */
internal object GatewayMultiplexer {
    private const val WINDOW = 256 * 1024
    private val timer = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "gateway-mux-idle").apply { isDaemon = true } }
    private var currentKey: String? = null
    private var current: Session? = null

    fun socket(identity: GatewayIdentity.Identity, connect: (Int) -> Socket): Socket {
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(identity.cert.encoded)
        digest.update(identity.innerClient.encoded)
        digest.update(identity.innerServerPin)
        val key = identity.gateway + ":" + digest.digest().joinToString("") { "%02x".format(it) }
        return StreamSocket(key, connect)
    }

    @Synchronized private fun open(key: String, bridge: Socket, timeout: Int, connect: (Int) -> Socket): Stream {
        if (currentKey != key || current?.closed != false) {
            current?.close()
            current = null
            val physical = connect(timeout)
            try { current = Session(physical) } catch (error: Exception) { physical.close(); throw error }
            currentKey = key
        }
        return current!!.open(bridge, timeout)
    }

    @Synchronized internal fun close() { current?.close(); current = null; currentKey = null }

    private class StreamSocket(private val key: String, private val connectPhysical: (Int) -> Socket) : Socket() {
        @Volatile private var stream: Stream? = null
        override fun connect(endpoint: SocketAddress) = connect(endpoint, 25000)
        override fun connect(endpoint: SocketAddress, timeout: Int) {
            val limit = if (timeout > 0) timeout else 25000
            var bridge: Socket? = null
            try {
                ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { listener ->
                    listener.soTimeout = limit
                    super.connect(InetSocketAddress("127.0.0.1", listener.localPort), limit)
                    bridge = listener.accept().apply { tcpNoDelay = true }
                }
                stream = open(key, bridge!!, limit, connectPhysical)
                if (isClosed) { stream?.reset(); throw IOException("Соединение закрыто") }
            } catch (error: Exception) { runCatching { bridge?.close() }; close(); throw IOException("Не удалось открыть поток туннеля: ${error.message}", error) }
        }
        override fun close() { stream?.reset(); super.close() }
    }

    private class Session(private val socket: Socket) {
        private val input = DataInputStream(socket.getInputStream())
        private val output = DataOutputStream(socket.getOutputStream())
        private val writing = Any()
        private val streams = ConcurrentHashMap<Int, Stream>()
        private var nextID = 1
        @Volatile var closed = false
            private set
        init {
            output.write("AI-SECRETARY-MUX/1\n".toByteArray(Charsets.US_ASCII))
            output.flush()
            thread(name = "gateway-mux-reader", isDaemon = true) {
                try { readFrames() } catch (_: Exception) { /* Propagate EOF to all inner TLS sockets. */ }
                finally { close() }
            }
        }
        @Synchronized fun open(bridge: Socket, timeout: Int): Stream {
            if (closed) throw IOException("Туннель закрыт")
            if (streams.size >= 32 || nextID <= 0) throw IOException("Слишком много потоков туннеля")
            val stream = Stream(this, nextID, bridge)
            nextID += 2
            streams[stream.id] = stream
            try {
                frame(1, 1, stream.id, 0)
                if (!stream.accepted.await(timeout.toLong(), TimeUnit.MILLISECONDS) || stream.closed) {
                    throw IOException("Сервер не подтвердил общий туннель. Проверьте соединение и версию сервера")
                }
                stream.start()
                return stream
            } catch (error: Exception) { stream.reset(); throw error }
        }
        fun frame(type: Int, flags: Int, id: Int, length: Int, bytes: ByteArray? = null, offset: Int = 0) {
            try { synchronized(writing) {
                if (closed) throw IOException("Туннель закрыт")
                output.writeByte(0); output.writeByte(type); output.writeShort(flags)
                output.writeInt(id); output.writeInt(length)
                if (bytes != null) output.write(bytes, offset, length)
                output.flush()
            } } catch (error: IOException) { close(); throw error }
        }
        private fun readFrames() {
            while (!closed) {
                if (input.readUnsignedByte() != 0) throw IOException("Invalid mux version")
                val type = input.readUnsignedByte()
                val flags = input.readUnsignedShort()
                val id = input.readInt()
                val length = input.readInt()
                if (flags and 15 != flags) throw IOException("Invalid mux flags")
                when (type) {
                    0, 1 -> {
                        if (id <= 0 || id and 1 == 0 || flags and 1 != 0 || length < 0 || length > WINDOW) throw IOException("Invalid mux stream frame")
                        val bytes = if (type == 0 && length > 0) ByteArray(length).also { input.readFully(it) } else null
                        streams[id]?.receive(flags, if (type == 1) length else 0, bytes)
                    }
                    2 -> {
                        if (id != 0) throw IOException("Invalid mux ping")
                        if (flags == 1) frame(2, 2, 0, length)
                    }
                    3 -> { if (id != 0) throw IOException("Invalid mux shutdown"); return }
                    else -> throw IOException("Invalid mux frame type")
                }
            }
        }
        fun release(stream: Stream) {
            streams.remove(stream.id, stream)
            // Use the registry lock so idle expiry cannot race selection of this session.
            timer.schedule({ synchronized(GatewayMultiplexer) { closeIfIdle() } }, 1, TimeUnit.SECONDS)
        }
        @Synchronized private fun closeIfIdle() { if (streams.isEmpty()) close() }
        // Must not take the open() monitor: a failed reader must wake pending ACK waits.
        fun close() {
            closed = true
            runCatching { socket.close() }
            streams.values.toList().forEach { it.terminate() }
        }
    }

    private class Stream(private val session: Session, val id: Int, private val bridge: Socket) {
        val accepted = CountDownLatch(1)
        @Suppress("PLATFORM_CLASS_MAPPED_TO_KOTLIN") // wait/notify implement the per-stream flow-control window.
        private val monitor = Object()
        private val incoming = ArrayDeque<ByteArray>()
        private var sendWindow = WINDOW
        private var receiveWindow = WINDOW
        private var remoteEOF = false
        @Volatile var closed = false
            private set
        fun receive(flags: Int, window: Int, bytes: ByteArray?) {
            if (flags and 8 != 0) { terminate(); return }
            synchronized(monitor) {
                if (closed) return
                if (window > WINDOW - sendWindow) throw IOException("Invalid mux window update")
                sendWindow += window
                if (bytes != null) {
                    if (remoteEOF || bytes.size > receiveWindow) throw IOException("Mux receive window exceeded")
                    receiveWindow -= bytes.size
                    incoming.addLast(bytes)
                }
                if (flags and 4 != 0) remoteEOF = true
                if (flags and 2 != 0) accepted.countDown()
                monitor.notifyAll()
            }
        }
        fun start() {
            thread(name = "gateway-mux-send", isDaemon = true) {
                try {
                    val source = bridge.getInputStream()
                    val bytes = ByteArray(16384)
                    while (!closed) {
                        val count = source.read(bytes)
                        if (count < 0) break
                        var offset = 0
                        while (offset < count) {
                            val amount = synchronized(monitor) {
                                val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(30)
                                while (sendWindow == 0 && !closed) {
                                    val remaining = deadline - System.nanoTime()
                                    if (remaining <= 0) throw IOException("Mux write timeout")
                                    TimeUnit.NANOSECONDS.timedWait(monitor, remaining)
                                }
                                if (closed) throw IOException("Поток закрыт")
                                minOf(count - offset, sendWindow).also { sendWindow -= it }
                            }
                            session.frame(0, 0, id, amount, bytes, offset)
                            offset += amount
                        }
                    }
                } catch (_: Exception) { /* Reset only this logical connection. */ }
                finally { reset() }
            }
            thread(name = "gateway-mux-receive", isDaemon = true) {
                try {
                    val destination = bridge.getOutputStream()
                    while (!closed) {
                        val bytes = synchronized(monitor) {
                            while (incoming.isEmpty() && !remoteEOF && !closed) monitor.wait()
                            if (closed || incoming.isEmpty()) null else incoming.removeFirst()
                        } ?: break
                        destination.write(bytes)
                        synchronized(monitor) { receiveWindow += bytes.size }
                        session.frame(1, 0, id, bytes.size)
                    }
                    // Preserve buffered response bytes until the inner TLS reader drains them.
                    runCatching { bridge.shutdownOutput() }
                } catch (_: Exception) { reset() }
            }
        }
        fun reset() {
            if (!closed) runCatching { session.frame(1, 8, id, 0) }
            terminate()
        }
        fun terminate() {
            synchronized(monitor) {
                if (closed) return
                closed = true
                incoming.clear()
                monitor.notifyAll()
                accepted.countDown()
            }
            runCatching { bridge.close() }
            session.release(this)
        }
    }
}
