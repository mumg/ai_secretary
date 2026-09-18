package net.muratov.assistant

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Shader
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.google.zxing.RGBLuminanceSource
import com.journeyapps.barcodescanner.Size
import net.muratov.assistant.setup.DenseQRReader
import net.muratov.assistant.setup.IdentityQRDecoderFactory
import net.muratov.assistant.setup.QRPreviewStrategy
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DenseQRReaderTest {
    @Test fun bundledReaderDecodesDenseDirectQRWithPerspectiveAndUnevenLighting() {
        val assets = InstrumentationRegistry.getInstrumentation().context.assets
        val payload = assets.open("mobile-identity/compact-identity.txt").bufferedReader().use { it.readText() }
        val qr = assets.open("mobile-identity/compact-identity.png").use { BitmapFactory.decodeStream(it) }
        DenseQRReader().use { reader ->
            IdentityQRDecoderFactory().use { factory ->
                val combined = factory.createDecoder(null)
                for (tilted in listOf(false, true)) {
                    // Odd crop dimensions exercise NV21 padding as well as camera-like frames.
                    val frame = Bitmap.createBitmap(1001, 1101, Bitmap.Config.ARGB_8888)
                    val canvas = Canvas(frame)
                    canvas.drawColor(Color.WHITE)
                    val matrix = Matrix()
                    val size = qr.width.toFloat()
                    val target = if (tilted) floatArrayOf(125f,171f,842f,94f,875f,877f,78f,915f)
                        else floatArrayOf(140f,140f,860f,140f,860f,860f,140f,860f)
                    matrix.setPolyToPoly(floatArrayOf(0f,0f,size,0f,size,size,0f,size),0,target,0,4)
                    canvas.drawBitmap(qr,matrix,Paint(Paint.FILTER_BITMAP_FLAG))
                    canvas.drawRect(0f,0f,1001f,1101f,Paint().apply {
                        shader = LinearGradient(0f,0f,1001f,1101f,0x00ffffff,0x60ffffff,Shader.TileMode.CLAMP)
                    })
                    val pixels = IntArray(frame.width * frame.height)
                    frame.getPixels(pixels,0,frame.width,0,0,frame.width,frame.height)
                    val source = RGBLuminanceSource(frame.width,frame.height,pixels)
                    // Run the bundled reader itself, not just the fast ZXing path.
                    var result = reader.decode(source)
                    val deadline = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(10)
                    while (result == null && System.nanoTime() < deadline) {
                        Thread.sleep(100)
                        result = reader.decode(source)
                    }
                    assertEquals("Bundled reader, perspective=$tilted",payload,result?.text)
                    assertEquals("Camera decoder, perspective=$tilted",payload,combined.decode(source)?.text)
                    frame.recycle()
                }
            }
        }
        qr.recycle()
    }

    @Test fun squareQRGetsMoreSensorPixelsAndUncroppedPreview() {
        val strategy = QRPreviewStrategy()
        val sizes = mutableListOf(Size(2400,1080),Size(2560,1440),Size(1920,1440),Size(1920,1080),Size(640,480))
        assertEquals(Size(1920,1440),strategy.getBestPreviewSize(sizes,Size(2670,1200)))
        val portrait = strategy.scalePreview(Size(1440,1920),Size(1200,2670))
        assertEquals(1200,portrait.width())
        assertEquals(1600,portrait.height())
        assertTrue(portrait.top >= 0 && portrait.bottom <= 2670)
        assertEquals(Size(640,480),strategy.getBestPreviewSize(mutableListOf(Size(640,480)),Size(1920,1080)))
    }
}
