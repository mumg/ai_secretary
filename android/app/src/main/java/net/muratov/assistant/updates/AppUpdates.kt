package net.muratov.assistant.updates

import android.content.Context
import android.content.Intent
import android.net.Uri
import com.google.android.play.core.appupdate.AppUpdateManagerFactory
import com.google.android.play.core.install.model.UpdateAvailability as PlayUpdateAvailability
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import net.muratov.assistant.BuildConfig
import net.muratov.assistant.i18n.tr
import ru.rustore.sdk.appupdate.manager.factory.RuStoreAppUpdateManagerFactory
import ru.rustore.sdk.appupdate.model.UpdateAvailability as RuStoreUpdateAvailability
import java.io.File
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

enum class AppStore(val title: String, val appPackage: String, val uri: String, val webUrl: String) {
    PLAY("Google Play", "com.android.vending", "market://details?id=net.muratov.assistant",
        "https://play.google.com/store/apps/details?id=net.muratov.assistant"),
    RUSTORE("RuStore", "ru.vk.store", "rustore://apps.rustore.ru/app/net.muratov.assistant",
        "https://www.rustore.ru/catalog/app/net.muratov.assistant");

    fun open(context: Context): Boolean {
        val inStore = Intent(Intent.ACTION_VIEW, Uri.parse(uri)).setPackage(appPackage)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (runCatching { context.startActivity(inStore) }.isSuccess) return true
        return runCatching {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(webUrl))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }.isSuccess
    }
}

data class UpdateState(
    val busy: Boolean = false,
    val available: Boolean = false,
    val versionName: String? = null,
    val message: String? = null,
)

/** Checks only the assigned store. Downloads and installation remain in that store. */
class AppUpdates(private val context: Context) {
    val store = if (BuildConfig.PLAY_DISTRIBUTION) AppStore.PLAY else AppStore.RUSTORE
    private val preferences = context.getSharedPreferences("app_updates", Context.MODE_PRIVATE)
    private val mutex = Mutex()
    private val mutableState = MutableStateFlow(UpdateState())
    val state = mutableState.asStateFlow()

    init {
        // Remove APKs and metadata left by the old GitHub self-updater.
        if (preferences.contains("manifest") || preferences.contains("automatic")) {
            preferences.edit().clear().apply()
        }
        runCatching { File(context.cacheDir, "updates").deleteRecursively() }
    }

    suspend fun check(force: Boolean = false): Boolean = mutex.withLock {
        if (!force && mutableState.value.message != null &&
            System.currentTimeMillis() - preferences.getLong("checked_at", 0L) < TimeUnit.HOURS.toMillis(12)) {
            return@withLock true
        }
        mutableState.value = mutableState.value.copy(busy = true, message = tr("Проверяем обновления…"))
        try {
            val latest = if (store == AppStore.PLAY) checkPlay() else checkRuStore()
            val newVersion = latest?.takeIf { it.first > BuildConfig.VERSION_CODE }
            mutableState.value = UpdateState(available = newVersion != null,
                versionName = newVersion?.second,
                message = newVersion?.let { tr("Доступна версия {0}", it.second ?: it.first) }
                    ?: tr("Установлена актуальная версия"))
            preferences.edit().putLong("checked_at", System.currentTimeMillis()).apply()
            true
        } catch (cancelled: CancellationException) { throw cancelled
        } catch (_: Exception) {
            mutableState.value = UpdateState(message = tr("Не удалось проверить обновление в магазине"))
            false
        }
    }

    private suspend fun checkPlay(): Pair<Long, String?>? = withContext(Dispatchers.IO) {
        val info = AppUpdateManagerFactory.create(context).appUpdateInfo.await()
        if (info.updateAvailability() == PlayUpdateAvailability.UPDATE_AVAILABLE)
            info.availableVersionCode().toLong() to null else null
    }

    private suspend fun checkRuStore(): Pair<Long, String?>? = suspendCancellableCoroutine { continuation ->
        RuStoreAppUpdateManagerFactory.create(context).getAppUpdateInfo()
            .addOnSuccessListener { info ->
                if (continuation.isActive) continuation.resume(
                    if (info.updateAvailability == RuStoreUpdateAvailability.UPDATE_AVAILABLE)
                        info.availableVersionCode to info.availableVersionName else null)
            }
            .addOnFailureListener { failure ->
                if (continuation.isActive) continuation.resumeWithException(failure)
            }
    }
}
