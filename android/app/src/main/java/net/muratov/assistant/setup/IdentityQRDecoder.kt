package net.muratov.assistant.setup

import com.google.android.gms.tasks.Task
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import com.google.zxing.BarcodeFormat
import com.google.zxing.DecodeHintType
import com.google.zxing.LuminanceSource
import com.google.zxing.MultiFormatReader
import com.google.zxing.Result
import com.google.zxing.ResultPoint
import com.journeyapps.barcodescanner.Decoder
import com.journeyapps.barcodescanner.DecoderFactory
import java.io.Closeable
import java.util.concurrent.TimeUnit

/** Both readers run on the camera decoder thread. Never save/log frames or QR contents. */
internal class IdentityQRDecoderFactory : DecoderFactory, Closeable {
    private val fallback = DenseQRReader()
    override fun createDecoder(baseHints: MutableMap<DecodeHintType, *>?): Decoder {
        val hints = mutableMapOf<DecodeHintType, Any>(
            DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
            DecodeHintType.TRY_HARDER to true,
            DecodeHintType.ALSO_INVERTED to true,
        )
        baseHints?.forEach { (key, value) -> if (value != null) hints[key] = value }
        return object : Decoder(MultiFormatReader().apply { setHints(hints) }) {
            override fun decode(source: LuminanceSource): Result? = super.decode(source) ?: fallback.decode(source)
        }
    }
    override fun close() = fallback.close()
}

/** Bundled ML Kit model: ready on first launch, including without a network connection. */
internal class DenseQRReader : Closeable {
    private val scanner = BarcodeScanning.getClient(
        BarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build(),
    )
    private var pending: Task<List<Barcode>>? = null
    @Volatile private var closed = false

    fun decode(source: LuminanceSource): Result? {
        if (closed || pending?.isComplete == false) return null
        return try {
            // Camera crops can have odd dimensions. Pad the NV21 image with white
            // luminance and neutral chroma; keep the original pixels unscaled.
            val width = (source.width + 1) and -2
            val height = (source.height + 1) and -2
            val ySize = width * height
            val bytes = ByteArray(ySize + ySize / 2) { 128.toByte() }
            bytes.fill(255.toByte(), 0, ySize)
            val luminance = source.matrix
            for (row in 0 until source.height) {
                luminance.copyInto(bytes, row * width, row * source.width, (row + 1) * source.width)
            }
            val image = InputImage.fromByteArray(bytes, width, height, 0, InputImage.IMAGE_FORMAT_NV21)
            val task = scanner.process(image).also { pending = it }
            val code = Tasks.await(task, 2, TimeUnit.SECONDS).firstOrNull { it.rawValue != null } ?: return null
            val points = code.cornerPoints.orEmpty().map { ResultPoint(it.x.toFloat(), it.y.toFloat()) }.toTypedArray()
            Result(code.rawValue, code.rawBytes, points, BarcodeFormat.QR_CODE)
        } catch (_: Exception) {
            // A bad frame or a paused camera is normal. Do not queue another ML
            // task until this one has finished, even if initialization timed out.
            null
        }
    }
    override fun close() { closed = true; scanner.close() }
}
