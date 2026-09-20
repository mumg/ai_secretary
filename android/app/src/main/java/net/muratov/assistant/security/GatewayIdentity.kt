package net.muratov.assistant.security

import net.muratov.assistant.i18n.tr

import android.content.Context
import android.system.Os
import android.util.AtomicFile
import net.muratov.assistant.setup.normalizeServerUrl
import org.json.JSONObject
import java.io.File
import java.security.*
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Base64
import java.util.zip.InflaterInputStream
import javax.net.ssl.*

/** Gateway bootstrap is never backed up or installed into Android KeyChain. */
object GatewayIdentity {
    const val QR_PREFIX = "ai-secretary:gateway:v1:"
    private const val ALIAS_PREFIX = "gateway-identity:"
    data class Identity(val gateway: String, val installation: String, val cert: X509Certificate,
        val key: PrivateKey, val innerServerPin: ByteArray, val innerClient: X509Certificate, val innerKey: PrivateKey)
    fun isAlias(alias: String?) = alias?.startsWith(ALIAS_PREFIX) == true
    fun isQR(payload: String) = payload.startsWith("ai-secretary:gateway:") || payload.startsWith("AI-SECRETARY:G")
    private fun decode(value: String) = Base64.getUrlDecoder().decode(value)
    private fun cert(value: String, usage: String): X509Certificate = cert(decode(value), usage)
    private fun cert(bytes: ByteArray, usage: String): X509Certificate {
        val cert = CertificateFactory.getInstance("X.509").generateCertificate(bytes.inputStream()) as X509Certificate
        require(cert.encoded.contentEquals(bytes))
        cert.checkValidity()
        require(cert.basicConstraints == -1 && cert.extendedKeyUsage?.contains(usage) == true)
        return cert
    }
    private fun key(value: String, cert: X509Certificate): PrivateKey {
        val key = KeyFactory.getInstance("EC").generatePrivate(PKCS8EncodedKeySpec(decode(value)))
        return validateKey(key, cert)
    }
    private fun validateKey(key: PrivateKey, cert: X509Certificate): PrivateKey {
        val challenge = "AI Secretary gateway validation".toByteArray()
        val signature = Signature.getInstance("SHA256withECDSA").run { initSign(key); update(challenge); sign() }
        require(Signature.getInstance("SHA256withECDSA").run { initVerify(cert.publicKey); update(challenge); verify(signature) }) { tr("Ключ не соответствует сертификату") }
        return key
    }
    private fun parseCompact(payload: String): Identity {
        val fields = CompactIdentity.fields(payload, CompactIdentity.GATEWAY_PREFIX, 6)
        val gateway = requireNotNull(normalizeServerUrl(fields[0].toString(Charsets.UTF_8)))
        val cert = cert(fields[1], "1.3.6.1.5.5.7.3.2")
        val uris = cert.subjectAlternativeNames.orEmpty().filter { it[0] == 6 }.map { it[1].toString() }
        require(uris.size == 1)
        val id = Regex("spiffe://ai-secretary-gateway/installations/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/roles/client")
            .matchEntire(uris.single())?.groupValues?.get(1)
        require(id != null) { tr("Сертификат не соответствует роли или UID") }
        require(fields[3].size == 32)
        val innerClient = cert(fields[4], "1.3.6.1.5.5.7.3.2")
        return Identity(gateway, id, cert, validateKey(CompactIdentity.key(fields[2], cert), cert), fields[3],
            innerClient, validateKey(CompactIdentity.key(fields[5], innerClient), innerClient))
    }
    internal fun parse(payload: String): Identity {
        if (payload.startsWith(CompactIdentity.GATEWAY_PREFIX)) return parseCompact(payload)
        require(payload.length <= 8192 && payload.startsWith(QR_PREFIX)) { tr("Обновите приложение для чтения этого QR-кода") }
        val raw = InflaterInputStream(decode(payload.removePrefix(QR_PREFIX)).inputStream()).use { input ->
            val out = java.io.ByteArrayOutputStream()
            val chunk = ByteArray(1024)
            while (true) {
                val n = input.read(chunk); if (n < 0) break
                require(out.size() + n <= 16 * 1024) { tr("Слишком большой QR-пакет") }
                out.write(chunk, 0, n)
            }
            out.toByteArray()
        }
        val json = JSONObject(raw.toString(Charsets.UTF_8))
        require(json.getInt("v") == 1)
        val gateway = requireNotNull(normalizeServerUrl(json.getString("gateway")))
        val id = json.getString("installation_id")
        require(id.matches(Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")))
        val cert = cert(json.getString("cert"), "1.3.6.1.5.5.7.3.2")
        val uris = cert.subjectAlternativeNames.orEmpty().filter { it[0] == 6 }.map { it[1] }
        require(uris == listOf("spiffe://ai-secretary-gateway/installations/${id}/roles/client")) { tr("Сертификат не соответствует роли или UID") }
        val innerServer = cert(json.getString("inner_server"), "1.3.6.1.5.5.7.3.1")
        require(innerServer.subjectAlternativeNames.orEmpty().any { it[0] == 2 && it[1] == "secretary.internal" })
        val innerClient = cert(json.getString("inner_client"), "1.3.6.1.5.5.7.3.2")
        return Identity(gateway, id, cert, key(json.getString("key"), cert), MessageDigest.getInstance("SHA-256").digest(innerServer.encoded), innerClient, key(json.getString("inner_key"), innerClient))
    }
    private fun directory(context: Context) = File(context.noBackupFilesDir, "gateway-identities").apply {
        check(isDirectory || mkdirs()); Os.chmod(absolutePath, 448)
    }
    private fun file(context: Context, alias: String): File {
        require(isAlias(alias) && alias.removePrefix(ALIAS_PREFIX).matches(Regex("[a-f0-9]{64}")))
        return File(directory(context), alias.removePrefix(ALIAS_PREFIX) + ".json")
    }
    fun importQR(context: Context, payload: String): AppClientIdentity.Imported {
        val identity = parse(payload)
        val id = MessageDigest.getInstance("SHA-256").digest(payload.toByteArray()).joinToString("") { "%02x".format(it) }
        val alias = ALIAS_PREFIX + id
        val atomic = AtomicFile(file(context, alias))
        val output = atomic.startWrite()
        try {
            Os.fchmod(output.fd, 384); output.write(payload.toByteArray()); atomic.finishWrite(output)
        } catch (failure: Exception) { atomic.failWrite(output); throw failure }
        return AppClientIdentity.Imported(identity.gateway, alias)
    }
    fun load(context: Context, baseUrl: String, alias: String): Identity {
        val file = file(context, alias); require(file.length() <= 8192)
        val identity = parse(AtomicFile(file).openRead().use { it.readBytes().toString(Charsets.UTF_8) })
        require(identity.gateway == normalizeServerUrl(baseUrl)) { tr("Ключ выдан для другого гейтвея") }
        return identity
    }
    fun retainOnly(context: Context, alias: String?) {
        val keep = if (isAlias(alias)) file(context, alias!!).name else null
        directory(context).listFiles()?.filter { it.name != keep }?.forEach { it.delete() }
    }
    fun keyManagers(cert: X509Certificate, key: PrivateKey): Array<KeyManager> {
        val password = CharArray(0)
        val store = KeyStore.getInstance("PKCS12").apply { load(null, password); setKeyEntry("client", key, password, arrayOf(cert)) }
        return KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).run { init(store, password); keyManagers }
    }
    fun matchesInnerServer(identity: Identity, cert: java.security.cert.Certificate): Boolean =
        MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(cert.encoded), identity.innerServerPin)
    fun trustServer(identity: Identity) = object : X509TrustManager {
        override fun getAcceptedIssuers() = emptyArray<X509Certificate>()
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) { throw java.security.cert.CertificateException("Server trust only") }
        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            if (chain.size != 1 || !matchesInnerServer(identity, chain[0])) throw java.security.cert.CertificateException("Unexpected inner server")
            chain[0].checkValidity()
        }
    }
}
