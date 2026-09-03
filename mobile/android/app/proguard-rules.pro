# React Native core
-keep class com.facebook.react.** { *; }
-keep class com.facebook.hermes.** { *; }
-dontwarn com.facebook.react.**

# op-sqlite JSI bindings
-keep class com.op.sqlite.** { *; }

# TFLite: the interpreter is reached reflectively
-keep class org.tensorflow.lite.** { *; }
-dontwarn org.tensorflow.lite.**

# Geolocation service
-keep class com.agontuk.RNFusedLocation.** { *; }
