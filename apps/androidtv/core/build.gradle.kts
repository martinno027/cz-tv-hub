plugins {
    alias(libs.plugins.kotlin.jvm)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.junit)
}

// The golden fixtures are shared with the other apps; read them in place from the nearest
// `spec/fixtures` above this project, so the app directory can move without breaking tests.
val specFixtures: File = generateSequence(rootDir.absoluteFile) { it.parentFile }
    .map { it.resolve("spec/fixtures") }
    .firstOrNull { it.isDirectory }
    ?: throw GradleException("spec/fixtures not found in any parent of $rootDir")

sourceSets {
    test {
        resources.srcDir(specFixtures)
    }
}

// Prague-based logic must not lean on the device zone: run the tests in another one.
tasks.withType<Test>().configureEach {
    systemProperty("user.timezone", "America/New_York")
}
