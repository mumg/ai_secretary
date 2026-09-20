package net.muratov.assistant.ui

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.pm.ActivityInfo
import android.hardware.Sensor
import android.hardware.SensorManager
import android.os.Build
import android.os.Bundle
import androidx.window.WindowSdkExtensions
import androidx.window.layout.FoldingFeature
import androidx.window.layout.SupportedPosture
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowMetricsCalculator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

internal fun portraitOnly(isFoldable: Boolean, displaySmallestWidthDp: Int): Boolean =
    !isFoldable && displaySmallestWidthDp < 600

/** Device capability, not current window width or hinge posture, controls rotation. */
object OrientationPolicy {
    @SuppressLint("RequiresWindowSdk", "SourceLockedOrientationActivity")
    fun install(application: Application) {
        val preferences = application.getSharedPreferences("device-capabilities", Context.MODE_PRIVATE)
        val tracker = WindowInfoTracker.getOrCreate(application)
        val hinge = Build.VERSION.SDK_INT >= 30 &&
            application.getSystemService(SensorManager::class.java).getDefaultSensor(Sensor.TYPE_HINGE_ANGLE) != null
        val tabletop = WindowSdkExtensions.getInstance().extensionVersion >= 6 &&
            SupportedPosture.TABLETOP in tracker.supportedPostures
        // Older foldables may report FoldingFeature only on their inner display.
        // Remember that hardware capability so closing the device cannot lock it.
        var foldable = hinge || tabletop || preferences.getBoolean("foldable", false)
        val observers = mutableMapOf<Activity, CoroutineScope>()
        val activities = mutableSetOf<Activity>()
        fun apply(activity: Activity) {
            val bounds = WindowMetricsCalculator.getOrCreate().computeMaximumWindowMetrics(activity).bounds
            val width = (minOf(bounds.width(), bounds.height()) / activity.resources.displayMetrics.density).toInt()
            val desired = if (portraitOnly(foldable, width)) ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
                else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
            if (activity.requestedOrientation != desired) activity.requestedOrientation = desired
        }
        application.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, state: Bundle?) {
                activities.add(activity)
                apply(activity)
            }
            override fun onActivityStarted(activity: Activity) {
                val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
                observers.remove(activity)?.cancel()
                observers[activity] = scope
                scope.launch {
                    tracker.windowLayoutInfo(activity).collect { info ->
                        if (!foldable && info.displayFeatures.any { it is FoldingFeature }) {
                            foldable = true
                            preferences.edit().putBoolean("foldable", true).apply()
                            activities.toList().forEach(::apply)
                        }
                        apply(activity)
                    }
                }
            }
            override fun onActivityResumed(activity: Activity) = apply(activity)
            override fun onActivityPaused(activity: Activity) = Unit
            override fun onActivityStopped(activity: Activity) { observers.remove(activity)?.cancel() }
            override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) = Unit
            override fun onActivityDestroyed(activity: Activity) {
                observers.remove(activity)?.cancel()
                activities.remove(activity)
            }
        })
    }
}
