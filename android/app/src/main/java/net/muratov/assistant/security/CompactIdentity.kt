package net.muratov.assistant.security

import java.io.ByteArrayOutputStream
import java.math.BigInteger
import java.security.KeyFactory
import java.security.PrivateKey
import java.security.cert.X509Certificate
import java.security.interfaces.ECPublicKey
import java.security.spec.ECPrivateKeySpec
import java.util.zip.Inflater

/** Version 2: Base45(DEFLATE(uint16 length + binary field, repeated)). */
internal object CompactIdentity {
    const val DIRECT_PREFIX = "AI-SECRETARY:D2:"
    const val GATEWAY_PREFIX = "AI-SECRETARY:G2:"
    private const val ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

    fun fields(payload: String, prefix: String, count: Int): List<ByteArray> {
        require(payload.length <= 8192 && payload.startsWith(prefix)) { "Некорректный QR-код" }
        val text = payload.removePrefix(prefix)
        require(text.isNotEmpty() && text.length % 3 != 1)
        val zipped = ByteArrayOutputStream()
        var position = 0
        while (position < text.length) {
            val size = minOf(3, text.length - position)
            var value = 0
            var multiplier = 1
            repeat(size) {
                val digit = ALPHABET.indexOf(text[position++])
                require(digit >= 0)
                value += digit * multiplier
                multiplier *= 45
            }
            require(value <= if (size == 3) 65535 else 255)
            if (size == 3) zipped.write(value / 256)
            zipped.write(value % 256)
        }
        val inflater = Inflater()
        val out = ByteArrayOutputStream()
        try {
            inflater.setInput(zipped.toByteArray())
            val buffer = ByteArray(1024)
            while (!inflater.finished()) {
                val n = inflater.inflate(buffer)
                require(out.size() + n <= 16384) { "Слишком большой QR-пакет" }
                out.write(buffer, 0, n)
                require(n > 0 || inflater.finished()) { "Повреждённый QR-пакет" }
            }
            require(inflater.remaining == 0)
        } finally { inflater.end() }
        val raw = out.toByteArray()
        position = 0
        val fields = List(count) {
            require(position + 2 <= raw.size)
            val size = (raw[position].toInt() and 255) * 256 + (raw[position + 1].toInt() and 255)
            position += 2
            require(size in 1..4096 && position + size <= raw.size)
            raw.copyOfRange(position, position + size).also { position += size }
        }
        require(position == raw.size)
        return fields
    }

    fun key(scalar: ByteArray, cert: X509Certificate): PrivateKey {
        require(scalar.size == 32)
        val public = cert.publicKey as? ECPublicKey ?: error("Требуется ключ EC")
        require(public.params.order == BigInteger("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16))
        val value = BigInteger(1, scalar)
        require(value.signum() > 0 && value < public.params.order)
        return KeyFactory.getInstance("EC").generatePrivate(ECPrivateKeySpec(value, public.params))
    }
}
