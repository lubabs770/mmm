plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Version comes from the release tag in CI (-PmmmVersion=1.2.3); local builds get 0.0.0-dev.
val mmmVersionName = (findProperty("mmmVersionName") as String?) ?: "0.0.0-dev"
val mmmVersionCode = ((findProperty("mmmVersionCode") as String?) ?: "1").toInt()

android {
    namespace = "lubabs770.mmm.modemizer"
    compileSdk = 34

    defaultConfig {
        applicationId = "lubabs770.mmm.modemizer"
        minSdk = 25
        targetSdk = 34
        versionCode = mmmVersionCode
        versionName = mmmVersionName
        // The app is pure Kotlin; a universal APK covers every CPU type.
    }

    signingConfigs {
        // Release signing key comes from CI secrets, decoded to app/release.jks by the
        // workflow. Never committed: a leaked key could sign an "update" to a
        // privileged app with full phone access.
        create("release") {
            val store = System.getenv("MMM_KEYSTORE_FILE")
            if (store != null) {
                storeFile = file(store)
                storePassword = System.getenv("MMM_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MMM_KEY_ALIAS")
                keyPassword = System.getenv("MMM_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (System.getenv("MMM_KEYSTORE_FILE") != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    lint {
        // MODIFY_PHONE_STATE / READ_PRIVILEGED_PHONE_STATE are held only when MMM is
        // installed as a priv-app with the generated allowlist; see magisk/.
        disable += "ProtectedPermissions"
        abortOnError = true
        warningsAsErrors = false
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-service:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    val composeBom = platform("androidx.compose:compose-bom:2024.09.03")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    debugImplementation("androidx.compose.ui:ui-tooling")

    testImplementation("junit:junit:4.13.2")
    // Real org.json for unit tests: android.jar's copy is a stub that returns defaults.
    testImplementation("org.json:json:20231013")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
}
