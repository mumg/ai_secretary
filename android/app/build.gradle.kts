import java.util.Properties

plugins {
    id("com.android.application")
    id("com.google.devtools.ksp")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
    id("androidx.room")
}

val secretaryLocalProperties = Properties().apply {
    val file = rootProject.file("secretary.local.properties")
    if (file.isFile) {
        file.inputStream().use(::load)
    }
}
val secretaryServerUrl = providers.gradleProperty("secretaryServerUrl").orNull
    ?: secretaryLocalProperties.getProperty("serverUrl")
    ?: "https://localhost"
val secretaryClientCertificateResource =
    secretaryLocalProperties.getProperty("clientCertificateResource") ?: ""
val secretaryClientCertificatePassword =
    secretaryLocalProperties.getProperty("clientCertificatePassword") ?: ""
val secretarySigningStoreFile = providers.environmentVariable("SECRETARY_SIGNING_STORE_FILE").orNull
val secretarySigningStorePassword =
    providers.environmentVariable("SECRETARY_SIGNING_STORE_PASSWORD").orNull
val secretarySigningKeyAlias = providers.environmentVariable("SECRETARY_SIGNING_KEY_ALIAS").orNull
val secretarySigningKeyPassword =
    providers.environmentVariable("SECRETARY_SIGNING_KEY_PASSWORD").orNull
val secretaryReleaseSigningConfigured = listOf(
    secretarySigningStoreFile,
    secretarySigningStorePassword,
    secretarySigningKeyAlias,
    secretarySigningKeyPassword,
).all { !it.isNullOrBlank() }

if (file("google-services.json").isFile) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "net.muratov.assistant"
    compileSdk = 36

    defaultConfig {
        applicationId = "net.muratov.assistant"
        minSdk = 26
        targetSdk = 36
        versionCode = 9
        versionName = "0.4.4"
        manifestPlaceholders["usesCleartextTraffic"] = "false"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables.useSupportLibrary = true
    }

    signingConfigs {
        if (secretaryReleaseSigningConfigured) {
            create("release") {
                storeFile = file(secretarySigningStoreFile!!)
                storePassword = secretarySigningStorePassword
                keyAlias = secretarySigningKeyAlias
                keyPassword = secretarySigningKeyPassword
            }
        }
    }

    buildTypes {
        debug {
            buildConfigField("String", "DEFAULT_SERVER_URL", "\"$secretaryServerUrl\"")
            buildConfigField(
                "String",
                "CLIENT_CERT_RESOURCE",
                "\"$secretaryClientCertificateResource\"",
            )
            buildConfigField(
                "String",
                "CLIENT_CERT_PASSWORD",
                "\"$secretaryClientCertificatePassword\"",
            )
            manifestPlaceholders["usesCleartextTraffic"] = "false"
        }
        release {
            buildConfigField("String", "DEFAULT_SERVER_URL", "\"$secretaryServerUrl\"")
            buildConfigField("String", "CLIENT_CERT_RESOURCE", "\"\"")
            buildConfigField("String", "CLIENT_CERT_PASSWORD", "\"\"")
            signingConfig = signingConfigs.findByName("release")
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

room {
    schemaDirectory("$projectDir/schemas")
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.08.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.9.3")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")

    implementation("androidx.room:room-runtime:2.8.5")
    implementation("androidx.room:room-ktx:2.8.5")
    ksp("androidx.room:room-compiler:2.8.5")

    implementation("com.squareup.okhttp3:okhttp:5.1.0")
    implementation("com.squareup.retrofit2:retrofit:3.0.0")
    implementation("com.squareup.retrofit2:converter-gson:3.0.0")
    implementation("io.noties.markwon:core:4.6.2")

    implementation(platform("com.google.firebase:firebase-bom:34.18.0"))
    implementation("com.google.firebase:firebase-messaging")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.10.2")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
