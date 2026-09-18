package net.muratov.assistant

import android.graphics.BitmapFactory
import android.graphics.Bitmap
import net.muratov.assistant.security.CompactIdentity
import java.security.Signature
import android.system.Os
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.RGBLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import net.muratov.assistant.data.ApiFactory
import net.muratov.assistant.security.AppClientIdentity
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import javax.net.ssl.X509KeyManager

@RunWith(AndroidJUnit4::class)
class AppClientIdentityTest {
    @Test fun generatedServerQRImportsPrivatelyAndAuthenticatesWithMatchingKey() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val assets = instrumentation.context.assets
        val payload = assets.open("mobile-identity/test-identity.txt").bufferedReader().use { it.readText() }
        val bitmap = assets.open("mobile-identity/test-identity.png").use { BitmapFactory.decodeStream(it) }
        val pixels = IntArray(bitmap.width * bitmap.height)
        bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
        val decoded = QRCodeReader().decode(BinaryBitmap(HybridBinarizer(RGBLuminanceSource(bitmap.width, bitmap.height, pixels)))).text
        assertEquals(payload, decoded)
        val imported = AppClientIdentity.importQR(context, decoded)
        try {
            assertEquals("https://secretary.example.test", imported.server)
            assertTrue(AppClientIdentity.isAppAlias(imported.alias))
            val file = File(context.noBackupFilesDir, "client-identities/${imported.alias.removePrefix("app-identity:")}.json")
            assertTrue(file.isFile)
            assertEquals(0, Os.stat(file.absolutePath).st_mode and 0x3f) // No group/other permissions.
            val manager = AppClientIdentity.keyManagers(context, imported.server, imported.alias).filterIsInstance<X509KeyManager>().single()
            val keyAlias = manager.getClientAliases("EC", null).first()
            assertNotNull(manager.getPrivateKey(keyAlias))
            assertEquals("EC", manager.getCertificateChain(keyAlias).first().publicKey.algorithm)
            val client = ApiFactory.client(context, imported.server, imported.alias)
            assertFalse(client.followRedirects)
            assertThrows(IllegalArgumentException::class.java) {
                AppClientIdentity.keyManagers(context, "https://other.example.test", imported.alias)
            }
            val json = JSONObject(payload.removePrefix("ai-secretary:identity:"))
            json.put("server", "http://secretary.example.test")
            assertThrows(IllegalArgumentException::class.java) {
                AppClientIdentity.importQR(context, "ai-secretary:identity:$json")
            }
            assertThrows(IllegalArgumentException::class.java) { AppClientIdentity.importQR(context, "https://example.test") }
            assertThrows(IllegalArgumentException::class.java) {
                AppClientIdentity.keyManagers(context, imported.server, "app-identity:../../escape")
            }
            json.put("server", imported.server)
            json.put("key", java.util.Base64.getEncoder().encodeToString(
                java.security.KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair().private.encoded,
            ))
            assertThrows(IllegalArgumentException::class.java) {
                AppClientIdentity.importQR(context, "ai-secretary:identity:$json")
            }
            // Failed imports leave the working identity untouched.
            assertTrue(file.isFile)
            assertNotNull(AppClientIdentity.keyManagers(context, imported.server, imported.alias))
        } finally {
            File(context.noBackupFilesDir, "client-identities/${imported.alias.removePrefix("app-identity:")}.json").delete()
        }
    }
    @Test fun compactServerQRScansAtScreenSizeAndAuthenticates() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val assets = instrumentation.context.assets
        val payload = assets.open("mobile-identity/compact-identity.txt").bufferedReader().use { it.readText() }
        val original = assets.open("mobile-identity/compact-identity.png").use { BitmapFactory.decodeStream(it) }
        for (available in listOf(original.width, 360, 480)) {
            val modules = original.width / 6
            val size = modules * (available / modules)
            val bitmap = Bitmap.createScaledBitmap(original, size, size, false)
            val pixels = IntArray(size * size)
            bitmap.getPixels(pixels, 0, size, 0, 0, size, size)
            try {
                assertEquals(payload, QRCodeReader().decode(BinaryBitmap(HybridBinarizer(RGBLuminanceSource(size, size, pixels))), mapOf(DecodeHintType.TRY_HARDER to true)).text)
            } catch (e: Exception) { throw AssertionError("Direct QR at $size pixels", e) }
        }
        val imported = AppClientIdentity.importQR(context, payload)
        try {
            val manager = AppClientIdentity.keyManagers(context, imported.server, imported.alias).filterIsInstance<X509KeyManager>().single()
            val alias = manager.getClientAliases("EC", null).first()
            val challenge = "compact QR authentication".toByteArray()
            val signature = Signature.getInstance("SHA256withECDSA").run { initSign(manager.getPrivateKey(alias)); update(challenge); sign() }
            assertTrue(Signature.getInstance("SHA256withECDSA").run { initVerify(manager.getCertificateChain(alias).first()); update(challenge); verify(signature) })
            val fields = CompactIdentity.fields(payload, CompactIdentity.DIRECT_PREFIX, 3)
            val badOrigin = fields.toMutableList().apply { this[0] = "http://example.test".toByteArray() }
            assertThrows(IllegalArgumentException::class.java) { AppClientIdentity.importQR(context, compactTestPayload(CompactIdentity.DIRECT_PREFIX, badOrigin)) }
            val badKey = fields.toMutableList().apply { this[2] = ByteArray(32) }
            assertThrows(IllegalArgumentException::class.java) { AppClientIdentity.importQR(context, compactTestPayload(CompactIdentity.DIRECT_PREFIX, badKey)) }
            assertThrows(IllegalArgumentException::class.java) { AppClientIdentity.importQR(context, compactTestRaw(CompactIdentity.DIRECT_PREFIX, ByteArray(16385))) }
            assertThrows(IllegalArgumentException::class.java) { AppClientIdentity.importQR(context, payload + "0") }
            assertThrows(IllegalArgumentException::class.java) { AppClientIdentity.importQR(context, payload.replace(":D2:", ":D3:")) }
            assertNotNull(AppClientIdentity.keyManagers(context, imported.server, imported.alias))
        } finally { File(context.noBackupFilesDir, "client-identities/${imported.alias.removePrefix("app-identity:")}.json").delete() }
    }

}
