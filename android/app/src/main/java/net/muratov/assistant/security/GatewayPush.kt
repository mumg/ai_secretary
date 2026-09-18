package net.muratov.assistant.security

import android.content.Context
import android.system.Os
import android.util.AtomicFile
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.File
import java.security.SecureRandom
import java.util.Base64
import java.util.UUID

object GatewayPush {
    /** Stable per-installation device key and monotonically increasing token revision. */
    @Synchronized fun register(context: Context, identity: GatewayIdentity.Identity, token: String) {
        val file = AtomicFile(File(context.noBackupFilesDir, "gateway-push-${identity.installation}.json"))
        val state = if (file.baseFile.exists()) file.openRead().bufferedReader().use { JSONObject(it.readText()) }
        else JSONObject().put("device_id", UUID.randomUUID().toString())
            .put("key", Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32).also { SecureRandom().nextBytes(it) }))
            .put("revision", 0L)
        if (state.optString("token") != token) {
            state.put("token", token).put("revision", state.getLong("revision") + 1)
        }
        val output = file.startWrite()
        try { Os.fchmod(output.fd, 384); output.write(state.toString().toByteArray()); file.finishWrite(output) }
        catch (e: Exception) { file.failWrite(output); throw e }
        val body = JSONObject().put("fcm_token", token).put("revision", state.getLong("revision"))
        val client = GatewayTransport.externalClient(identity)
        try {
            client.newCall(Request.Builder().url("${identity.gateway}/api/v1/devices/${state.getString("device_id")}")
                .header("X-Device-Key", state.getString("key"))
                .put(body.toString().toRequestBody("application/json".toMediaType())).build()).execute().use {
                if (!it.isSuccessful) throw java.io.IOException("Регистрация push на гейтвее: HTTP ${it.code}")
            }
        } finally { client.connectionPool.evictAll(); client.dispatcher.executorService.shutdown() }
    }
}
