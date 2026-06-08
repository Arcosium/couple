plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "uk.aive.couple"
    compileSdk = 35
    buildToolsVersion = "35.0.0"   // 빌드 이미지에 있는 버전으로 고정(34 다운로드 시도 방지)

    defaultConfig {
        applicationId = "uk.aive.couple"
        minSdk = 30
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"

        // 래핑할 웹앱 주소 — 백엔드 변경 시 여기만 고치고 재빌드.
        buildConfigField("String", "COUPLE_URL", "\"https://couple.ai-ve.uk\"")
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    buildTypes {
        release {
            // 개인용 debug APK 가 기본 — release 도 난독화 없이 단순 빌드 가능하게.
            isMinifyEnabled = false
        }
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)      // NotificationCompat, ContextCompat
    implementation(libs.androidx.activity.ktx)   // ComponentActivity, ActivityResult API
}
