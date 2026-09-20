package net.muratov.assistant.updates

import net.muratov.assistant.i18n.tr

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.FileProvider
import androidx.core.content.pm.PackageInfoCompat
import androidx.work.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import net.muratov.assistant.BuildConfig
import net.muratov.assistant.MainActivity
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** This transport never uses the server's mTLS identity or server credentials. */
class AppUpdates(private val context: Context) {
    private val preferences = context.getSharedPreferences("app_updates", Context.MODE_PRIVATE)
    private val mutex = Mutex()
    private val directory = File(context.cacheDir, "updates").apply { mkdirs() }
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS)
        .callTimeout(3, TimeUnit.MINUTES)
        .addNetworkInterceptor { chain ->
            val url = chain.request().url
            require(url.isHttps && url.host in setOf("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")) {
                tr("Неизвестный адрес загрузки")
            }
            chain.proceed(chain.request())
        }.build()
    private val mutableState = MutableStateFlow(UpdateState())
    val state = mutableState.asStateFlow()
    var automatic: Boolean
        get() = preferences.getBoolean("automatic", true)
        set(value) {
            preferences.edit().putBoolean("automatic", value).apply()
            schedule()
        }

    init {
        runCatching { preferences.getString("manifest", null)?.let(::decode) }.getOrNull()
            ?.takeIf { it.versionCode > BuildConfig.VERSION_CODE }
            ?.let { mutableState.value = UpdateState(release = it, ready = apk(it).isFile) }
    }

    fun schedule() {
        val work = WorkManager.getInstance(context)
        if (!automatic) { work.cancelUniqueWork("android-updates"); return }
        work.enqueueUniquePeriodicWork("android-updates", ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<UpdateWorker>(12, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build())
    }

    private fun decode(text: String): UpdateManifest {
        val json = JSONObject(text)
        return UpdateManifest(json.getLong("versionCode"), json.getString("versionName"), json.getInt("minSdk"),
            json.getString("applicationId"), json.getString("apkUrl"), json.getString("sha256"), json.getLong("size"))
            .validate(context.packageName, Build.VERSION.SDK_INT)
    }

    suspend fun check(force: Boolean = false): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (!force && (!automatic || System.currentTimeMillis() - preferences.getLong("checked_at", 0) < TimeUnit.HOURS.toMillis(12))) {
                // A previous check on a metered network may still be waiting for Wi-Fi.
                try { downloadAutomatically(); return@withLock true
                } catch (cancelled: CancellationException) { throw cancelled
                } catch (failure: Exception) {
                    mutableState.value = mutableState.value.copy(busy = false, message = failure.message)
                    return@withLock false
                }
            }
            mutableState.value = mutableState.value.copy(busy = true, message = tr("Проверяем обновления…"))
            try {
                client.newCall(Request.Builder().url(UPDATE_MANIFEST_URL).build()).execute().use { response ->
                    if (response.code == 404) {
                        mutableState.value = UpdateState(message = tr("Публичных Android-сборок пока нет"))
                    } else {
                        if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
                        val source = response.body.source()
                        source.request(65_537)
                        require(source.buffer.size <= 65_536) { tr("Слишком большой манифест обновления") }
                        val text = source.buffer.readUtf8()
                        val release = decode(text)
                        if (release.versionCode <= BuildConfig.VERSION_CODE) {
                            mutableState.value = UpdateState(message = tr("Установлена актуальная версия"))
                            preferences.edit().remove("manifest").apply()
                            directory.listFiles()?.forEach { it.delete() }
                        } else {
                            preferences.edit().putString("manifest", text).apply()
                            mutableState.value = UpdateState(release = release, ready = apk(release).isFile,
                                message = tr("Доступна версия {0}" , release.versionName))
                        }
                    }
                }
                preferences.edit().putLong("checked_at", System.currentTimeMillis()).apply()
                downloadAutomatically()
                true
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (failure: Exception) {
                mutableState.value = mutableState.value.copy(busy = false, message = tr("Не удалось проверить обновление: {0}" , failure.message.orEmpty().take(140)))
                false
            } finally { mutableState.value = mutableState.value.copy(busy = false) }
        }
    }

    private suspend fun downloadAutomatically() {
        if (!automatic || mutableState.value.release == null || mutableState.value.ready) return
        val connectivity = context.getSystemService(android.net.ConnectivityManager::class.java)
        if (!connectivity.isActiveNetworkMetered) downloadLocked()
        else mutableState.value = mutableState.value.copy(message = tr("Обновление ждёт Wi-Fi. Можно скачать вручную."))
    }

    suspend fun download() = withContext(Dispatchers.IO) {
        mutex.withLock {
            try { downloadLocked()
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (failure: Exception) {
                mutableState.value = mutableState.value.copy(message = tr("Не удалось скачать обновление: {0}" , failure.message.orEmpty().take(140)))
            } finally { mutableState.value = mutableState.value.copy(busy = false) }
        }
    }

    private fun apk(release: UpdateManifest) = File(directory, "update-${release.versionCode}.apk")

    private suspend fun downloadLocked() {
        val release = mutableState.value.release ?: return
        val target = apk(release)
        if (target.isFile) {
            verify(target, release)
            mutableState.value = mutableState.value.copy(busy = false, ready = true)
            return
        }
        val partial = File(directory, "download.part")
        mutableState.value = mutableState.value.copy(busy = true, message = tr("Скачиваем {0}…" , release.versionName))
        try {
            client.newCall(Request.Builder().url(release.apkUrl).build()).execute().use { response ->
                if (!response.isSuccessful) throw IOException("HTTP ${response.code}")
                response.body.byteStream().use { input -> partial.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var count = 0L
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        val length = input.read(buffer)
                        if (length < 0) break
                        count += length
                        require(count <= release.size) { tr("Размер APK не совпадает") }
                        output.write(buffer, 0, length)
                    }
                } }
            }
            verify(partial, release)
            check(partial.renameTo(target)) { tr("Не удалось сохранить APK") }
            directory.listFiles()?.filter { it != target }?.forEach { it.delete() }
            mutableState.value = mutableState.value.copy(busy = false, ready = true, message = tr("Обновление готово к установке"))
            notifyReady(release)
        } finally { partial.delete(); mutableState.value = mutableState.value.copy(busy = false) }
    }

    @Suppress("DEPRECATION")
    private fun verify(file: File, release: UpdateManifest) {
        try {
            require(file.length() == release.size) { tr("APK загружен не полностью") }
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buffer = ByteArray(64 * 1024)
                while (true) { val size = input.read(buffer); if (size < 0) break; digest.update(buffer, 0, size) }
            }
            require(digest.digest().joinToString("") { "%02x".format(it) } == release.sha256) { tr("Контрольная сумма APK не совпадает") }
            val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
            val installed = context.packageManager.getPackageInfo(context.packageName, flags)
            val archive = context.packageManager.getPackageArchiveInfo(file.path, flags) ?: error(tr("Некорректный APK"))
            require(archive.packageName == context.packageName && PackageInfoCompat.getLongVersionCode(archive) == release.versionCode &&
                release.versionCode > PackageInfoCompat.getLongVersionCode(installed)) { tr("Пакет или версия APK не совпадают") }
            fun signatures(info: android.content.pm.PackageInfo): Set<String> =
                (if (Build.VERSION.SDK_INT >= 28) info.signingInfo?.apkContentsSigners else info.signatures)
                    .orEmpty().map { it.toCharsString() }.toSet()
            val trusted = signatures(installed)
            require(trusted.isNotEmpty() && signatures(archive) == trusted) {
                tr("APK подписан другим ключом. Для перехода с debug нужна установка release-сборки.")
            }
        } catch (failure: Exception) {
            file.delete()
            mutableState.value = mutableState.value.copy(ready = false, message = failure.message)
            throw failure
        }
    }

    suspend fun installIntent(): Intent = withContext(Dispatchers.IO) {
        mutex.withLock {
            val release = state.value.release ?: error(tr("Нет обновления"))
            val file = apk(release)
            verify(file, release)
            Intent(Intent.ACTION_VIEW).setDataAndType(
                FileProvider.getUriForFile(context, "${context.packageName}.updates", file),
                "application/vnd.android.package-archive",
            ).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
    }

    private fun notifyReady(release: UpdateManifest) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("app_updates", tr("Обновления приложения"), NotificationManager.IMPORTANCE_DEFAULT))
        if (!manager.areNotificationsEnabled()) return
        val intent = PendingIntent.getActivity(context, 4501, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        manager.notify(4501, NotificationCompat.Builder(context, "app_updates")
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(tr("AI Секретарь {0}" , release.versionName))
            .setContentText(tr("Обновление скачано. Откройте приложение для установки."))
            .setContentIntent(intent).setAutoCancel(true).build())
    }
}

data class UpdateState(val busy: Boolean = false, val message: String? = null,
    val release: UpdateManifest? = null, val ready: Boolean = false)

class UpdateWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val updates = (applicationContext as net.muratov.assistant.ImproverApplication).container.updates
        return if (updates.check()) Result.success() else Result.retry()
    }
}
