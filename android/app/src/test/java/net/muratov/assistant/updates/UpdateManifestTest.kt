package net.muratov.assistant.updates

import org.junit.Assert.*
import org.junit.Test

class UpdateManifestTest {
    private val valid = UpdateManifest(12, "0.5.0", 26, "net.muratov.assistant",
        "https://github.com/mumg/ai_secretary/releases/download/android-v0.5.0-12/ai-secretary-0.5.0.apk", "a".repeat(64), 10_000_000)
    @Test fun acceptsReleaseFromOurRepository() {
        assertEquals(valid, valid.validate("net.muratov.assistant", 36))
    }
    @Test fun rejectsWrongPackageOrTamperedDownloadMetadata() {
        val bad = listOf(valid.copy(applicationId = "evil.app"), valid.copy(versionCode = 0),
            valid.copy(sha256 = "invalid"), valid.copy(size = 0), valid.copy(size = 201_000_000),
            valid.copy(minSdk = 37), valid.copy(apkUrl = valid.apkUrl.replace("https", "http")),
            valid.copy(apkUrl = valid.apkUrl.replace("mumg", "attacker")),
            valid.copy(apkUrl = valid.apkUrl + "?redirect=evil"),
            valid.copy(apkUrl = valid.apkUrl.replace("github.com", "github.com.evil.test")),
            valid.copy(versionCode = 13), valid.copy(versionName = "0.5.1"))
        for (manifest in bad) assertThrows(IllegalArgumentException::class.java) { manifest.validate("net.muratov.assistant", 36) }
    }
}
