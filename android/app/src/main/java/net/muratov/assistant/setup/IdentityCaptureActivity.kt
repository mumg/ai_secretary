package net.muratov.assistant.setup

import android.os.Bundle
import android.view.WindowManager
import com.journeyapps.barcodescanner.DecoratedBarcodeView
import com.journeyapps.barcodescanner.CaptureActivity
import com.journeyapps.barcodescanner.Size
import com.journeyapps.barcodescanner.camera.CameraSettings
import com.journeyapps.barcodescanner.camera.FitCenterStrategy

class IdentityCaptureActivity : CaptureActivity() {
    private var decoder: IdentityQRDecoderFactory? = null
    override fun onCreate(savedInstanceState: Bundle?) {
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        super.onCreate(savedInstanceState)
        val preview = findViewById<DecoratedBarcodeView>(com.google.zxing.client.android.R.id.zxing_barcode_scanner).barcodeView
        preview.cameraSettings.focusMode = CameraSettings.FocusMode.CONTINUOUS
        preview.previewScalingStrategy = QRPreviewStrategy()
        preview.marginFraction = 0.06
        preview.decoderFactory = IdentityQRDecoderFactory().also { decoder = it }
    }
    override fun onDestroy() {
        super.onDestroy()
        decoder?.close()
        decoder = null
    }
}

/** Dense, square QR codes benefit from the short side, not a panoramic preview. */
internal class QRPreviewStrategy : FitCenterStrategy() {
    override fun getBestPreviewSize(sizes: MutableList<Size>, desired: Size?): Size {
        val bounded = sizes.filter { it.width.toLong() * it.height <= 3_000_000 }
        return (bounded.ifEmpty { sizes }).maxWith(
            compareBy<Size> { minOf(it.width, it.height) }.thenBy { -it.width.toLong() * it.height },
        )
    }
}
