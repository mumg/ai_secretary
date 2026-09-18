package net.muratov.assistant.security

import android.content.Context
import android.util.AtomicFile
import android.system.Os
import org.json.JSONObject
import java.io.File
import java.security.KeyFactory
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64
import javax.net.ssl.KeyManager
import javax.net.ssl.KeyManagerFactory
import net.muratov.assistant.setup.normalizeServerUrl

/** App-private identity, never installed into Android KeyChain or exported via FileProvider. */
object AppClientIdentity {
    private const val PREFIX = "app-identity:"
    private const val QR_PREFIX = "ai-secretary:identity:"
    data class Imported(val server: String, val alias: String)
    private data class Identity(val server: String, val key: java.security.PrivateKey, val cert: X509Certificate)

    fun isAppAlias(alias: String?): Boolean = alias?.startsWith(PREFIX) == true

    private fun parse(payload: String): Identity {
        require(payload.length <= 4096 && payload.startsWith(QR_PREFIX)) { "Это не QR-код подключения AI Секретаря" }
        val json = JSONObject(payload.removePrefix(QR_PREFIX))
        require(json.getInt("v") == 1) { "Обновите приложение для чтения этого QR-кода" }
        val server = normalizeServerUrl(json.getString("server"))
        require(server != null) { "В QR-коде некорректный HTTPS-адрес сервера" }
        val cert = CertificateFactory.getInstance("X.509").generateCertificate(
            Base64.getDecoder().decode(json.getString("cert")).inputStream(),
        ) as X509Certificate
        cert.checkValidity()
        require(cert.basicConstraints == -1 && cert.extendedKeyUsage?.contains("1.3.6.1.5.5.7.3.2") == true) {
            "QR-код не содержит клиентский сертификат"
        }
        val key = KeyFactory.getInstance("EC").generatePrivate(
            PKCS8EncodedKeySpec(Base64.getDecoder().decode(json.getString("key"))),
        )
        val challenge = "AI Secretary identity validation".toByteArray()
        val signature = Signature.getInstance("SHA256withECDSA").run { initSign(key); update(challenge); sign() }
        require(Signature.getInstance("SHA256withECDSA").run { initVerify(cert.publicKey); update(challenge); verify(signature) }) {
            "Ключ не соответствует сертификату"
        }
        return Identity(server, key, cert)
    }

    private fun directory(context: Context) = File(context.noBackupFilesDir, "client-identities").apply {
        check(isDirectory || mkdirs()) { "Не удалось открыть каталог ключей приложения" }
        Os.chmod(absolutePath, 448) // 0700
    }

    private fun file(context: Context, alias: String): File {
        val id = alias.removePrefix(PREFIX)
        require(isAppAlias(alias) && id.matches(Regex("[a-f0-9]{64}"))) { "Некорректный идентификатор ключа" }
        return File(directory(context), "$id.json")
    }

    fun importQR(context: Context, payload: String): Imported {
        val identity = parse(payload)
        val id = MessageDigest.getInstance("SHA-256").digest(payload.toByteArray()).joinToString("") { "%02x".format(it) }
        val alias = PREFIX + id
        val atomic = AtomicFile(file(context, alias))
        val output = atomic.startWrite()
        try {
            Os.fchmod(output.fd, 384) // 0600
            output.write(payload.toByteArray(Charsets.UTF_8))
            atomic.finishWrite(output)
        } catch (failure: Exception) { atomic.failWrite(output); throw failure }
        return Imported(identity.server, alias)
    }

    fun keyManagers(context: Context, baseUrl: String, alias: String): Array<KeyManager> {
        val identityFile = file(context, alias)
        require(identityFile.length() <= 4096) { "Некорректный файл ключа" }
        val identity = parse(AtomicFile(identityFile).openRead().use { it.readBytes().toString(Charsets.UTF_8) })
        require(identity.server == normalizeServerUrl(baseUrl)) { "Этот ключ выдан для другого сервера. Отсканируйте его QR-код." }
        val password = CharArray(0)
        val store = KeyStore.getInstance("PKCS12").apply {
            load(null, password)
            setKeyEntry("client", identity.key, password, arrayOf(identity.cert))
        }
        return KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).run {
            init(store, password)
            keyManagers
        }
    }

    // Called only after successful connection and saving the selected identity.
    fun retainOnly(context: Context, alias: String?) {
        val keep = if (isAppAlias(alias)) file(context, alias!!).name else null
        directory(context).listFiles()?.filter { it.name != keep }?.forEach { it.delete() }
    }
}
