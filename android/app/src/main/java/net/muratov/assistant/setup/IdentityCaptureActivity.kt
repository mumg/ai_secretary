package net.muratov.assistant.setup

import android.os.Bundle
import android.view.WindowManager
import com.journeyapps.barcodescanner.CaptureActivity

class IdentityCaptureActivity : CaptureActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        super.onCreate(savedInstanceState)
    }
}
