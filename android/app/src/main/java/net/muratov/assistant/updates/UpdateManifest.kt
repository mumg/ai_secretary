package net.muratov.assistant.updates

import net.muratov.assistant.i18n.tr

import java.net.URI

const val UPDATE_MANIFEST_URL = "https://github.com/mumg/ai_secretary/releases/download/android-latest/android-update.json"
const val ANDROID_RELEASES_URL = "https://github.com/mumg/ai_secretary/releases/tag/android-latest"

data class UpdateManifest(
    val versionCode: Long,
    val versionName: String,
    val minSdk: Int,
    val applicationId: String,
    val apkUrl: String,
    val sha256: String,
    val size: Long,
) {
    fun validate(packageName: String, sdk: Int): UpdateManifest {
        require(versionCode > 0 && Regex("[0-9]+\\.[0-9]+\\.[0-9]+").matches(versionName)) { tr("Некорректная версия обновления") }
        require(applicationId == packageName) { tr("Обновление предназначено для другого приложения") }
        require(minSdk in 26..sdk) { tr("Для обновления нужна более новая версия Android") }
        require(Regex("[a-f0-9]{64}").matches(sha256)) { tr("Нет корректной контрольной суммы") }
        require(size in 1..200_000_000) { tr("Некорректный размер APK") }
        val uri = URI(apkUrl)
        require(uri.scheme == "https" && uri.host == "github.com" && uri.port == -1 &&
            uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null &&
            uri.rawPath == "/mumg/ai_secretary/releases/download/android-v${versionName}-${versionCode}/ai-secretary-${versionName}.apk") {
            tr("Неизвестный источник APK")
        }
        return this
    }
}
