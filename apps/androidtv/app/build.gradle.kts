import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

// Version line in Nastavení (spec/apps.md): build number = git commit count, short commit ("+" = uncommitted
// changes), build time in Prague. Stamped at build time; "?" without git.
fun git(vararg args: String): String? = try {
    providers.exec {
        commandLine("git", *args)
        isIgnoreExitValue = true
    }.standardOutput.asText.get().trim().ifEmpty { null }
} catch (_: Exception) {
    null
}

val buildNumber = git("rev-list", "--count", "HEAD")?.toIntOrNull()
val gitCommit = git("rev-parse", "--short", "HEAD")?.let { hash ->
    if (git("status", "--porcelain").isNullOrEmpty()) hash else "$hash+"
} ?: "?"
val buildTime: String = ZonedDateTime.now(ZoneId.of("Europe/Prague")).format(DateTimeFormatter.ofPattern("d. M. HH:mm"))

android {
    namespace = "cz.cztvhub.tv"
    compileSdk = 36

    defaultConfig {
        applicationId = "cz.cztvhub.tv"
        minSdk = 26
        targetSdk = 36
        versionCode = buildNumber ?: 1
        versionName = "0.1.0"
        buildConfigField("String", "BUILD_NUMBER", "\"${buildNumber ?: "?"}\"")
        buildConfigField("String", "GIT_COMMIT", "\"$gitCommit\"")
        buildConfigField("String", "BUILD_TIME", "\"$buildTime\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            // Sideloaded personal app: sign release builds with the debug key unless configured otherwise.
            signingConfig = signingConfigs.getByName("debug")
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

    // Media3's Format / TrackSelectionParameters call android.text.TextUtils (stubbed in JVM tests).
    testOptions.unitTests.isReturnDefaultValues = true
}

dependencies {
    implementation(project(":core"))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.ktx)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.tv.material)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.exoplayer.hls)
    implementation(libs.media3.ui)
    implementation(libs.kotlinx.coroutines.android)
    testImplementation(libs.junit)
}
