package net.muratov.assistant

import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.util.zip.DeflaterOutputStream

internal fun compactTestPayload(prefix: String, fields: List<ByteArray>): String {
    val raw = ByteArrayOutputStream()
    DataOutputStream(raw).use { out -> fields.forEach { out.writeShort(it.size); out.write(it) } }
    return compactTestRaw(prefix, raw.toByteArray())
}
internal fun compactTestRaw(prefix: String, raw: ByteArray): String {
    val zipped = ByteArrayOutputStream()
    DeflaterOutputStream(zipped).use { it.write(raw) }
    val bytes = zipped.toByteArray()
    val alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
    return prefix + buildString {
        var i = 0
        while (i < bytes.size) {
            val pair = i + 1 < bytes.size
            val value = if (pair) (bytes[i].toInt() and 255) * 256 + (bytes[i+1].toInt() and 255) else bytes[i].toInt() and 255
            append(alphabet[value % 45]); append(alphabet[value / 45 % 45])
            if (pair) append(alphabet[value / 2025])
            i += 2
        }
    }
}
