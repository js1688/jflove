plugins {
    id("com.android.application")
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.jflove.jflove_app"
    compileSdk = 36
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.jflove.jflove_app"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // 使用 debug 签名（开发阶段；正式发布需替换为 release keystore）
            signingConfig = signingConfigs.getByName("debug")
            // ⚠️ 不启用 R8 混淆/压缩：Flutter 的 Dart 编译器已做 tree-shaking，
            // R8 的激进优化（尤其是 proguard-android-optimize.txt）会破坏
            // Flutter 引擎和插件的反射/FFI 调用链，导致 release 包闪退或白屏。
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
