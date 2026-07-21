# Flutter 默认规则
-keep class io.flutter.app.** { *; }
-keep class io.flutter.plugin.**  { *; }
-keep class io.flutter.util.**  { *; }
-keep class io.flutter.view.**  { *; }
-keep class io.flutter.**  { *; }
-keep class io.flutter.plugins.**  { *; }

# Google Play Core 库（R8 报错缺少这些类）
-dontwarn com.google.android.play.core.**
-keep class com.google.android.play.core.** { *; }

# Dio 网络库
-keep class dio.** { *; }
-keep class com.lyokone.location.** { *; }

# PointyCastle 加密库（必须保留，否则加密功能失效）
-keep class org.bouncycastle.** { *; }
-keep class org.signal.client.** { *; }
-dontwarn org.bouncycastle.**
-dontwarn org.signal.client.**

# x25519 Dart 加密库
-keep class x25519.** { *; }
-dontwarn x25519.**

# Flutter Secure Storage
-keep class com.it_nomads.fluttersecurestorage.** { *; }

# 网络请求相关
-keepattributes *Annotation*
-keepattributes Signature
-keepattributes Exceptions

# 保留 Dart VM 相关的类（Flutter 内部使用）
-keep class dart.** { *; }
-dontwarn dart.**

# OkHttp（如果使用）
-dontwarn okhttp3.**
-dontwarn okio.**
-keep class okhttp3.** { *; }
-keep interface okhttp3.** { *; }

# 防止 R8 移除所有代码
-keep class ** { *; }
