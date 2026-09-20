package net.muratov.assistant.ui

import org.junit.Assert.*
import org.junit.Test

class OrientationPolicyTest {
    @Test fun ordinaryPhoneIsPortraitOnly() { assertTrue(portraitOnly(false, 393)) }
    @Test fun foldedAndUnfoldedPhonesCanRotate() {
        assertFalse(portraitOnly(true, 360))
        assertFalse(portraitOnly(true, 840))
    }
    @Test fun tabletsCanRotate() { assertFalse(portraitOnly(false, 600)) }
}
