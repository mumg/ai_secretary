package net.muratov.assistant.setup

import org.junit.Assert.*
import org.junit.Test

class ServerAddressTest {
    @Test fun validOrigins() {
        assertEquals("https://office.example.org", normalizeServerUrl("  https://office.example.org/  "))
        assertEquals("https://office.example.org:8443", normalizeServerUrl("https://office.example.org:8443"))
        assertEquals("https://[2001:db8::1]:443", normalizeServerUrl("https://[2001:db8::1]:443"))
    }
    @Test fun refusesDefaultsAndUnsafeInput() {
        for (address in listOf("", "office.example.org", "http://office.example.org", "https://user:pass@office.example.org",
            "https://office.example.org/path", "https://office.example.org?key=x", "https://office.example.org#x",
            "https://office.example.org:0", "https://office.example.org:65536", "javascript:alert(1)", "https:///foo")) {
            assertNull(address, normalizeServerUrl(address))
        }
    }
}
