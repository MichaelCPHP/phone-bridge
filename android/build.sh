#!/bin/bash
set -e
export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"

SDK="$HOME/Library/Android/sdk"
BUILD_TOOLS="$SDK/build-tools/34.0.0"
ANDROID_JAR=$(ls -d $SDK/platforms/android-*/android.jar 2>/dev/null | sort -V | tail -1)

echo "SDK:          $SDK"
echo "Build tools:  $BUILD_TOOLS"
echo "android.jar:  $ANDROID_JAR"
echo ""

if [ -z "$ANDROID_JAR" ]; then
    echo "ERROR: No android.jar found."
    exit 1
fi

OUT="/tmp/sms-bridge-apk/build"
SRC="/tmp/sms-bridge-apk/src/main"

rm -rf "$OUT" && mkdir -p "$OUT/compiled" "$OUT/dex" "$OUT/gen"

echo "Step 1: Compile resources with aapt2..."
$BUILD_TOOLS/aapt2 compile \
    --dir "$SRC/res" \
    -o "$OUT/compiled/"

echo "Step 2: Link resources..."
$BUILD_TOOLS/aapt2 link \
    "$OUT/compiled/"*.flat \
    -I "$ANDROID_JAR" \
    --manifest "$SRC/AndroidManifest.xml" \
    --java "$OUT/gen" \
    -o "$OUT/phone-bridge-unsigned.apk"

echo "Step 3: Compile Java..."
find "$SRC/java" -name "*.java" > /tmp/java_sources.txt
echo "$OUT/gen/com/phonebridge/R.java" >> /tmp/java_sources.txt

javac --release 8 \
    -classpath "$ANDROID_JAR" \
    -d "$OUT/compiled/" \
    @/tmp/java_sources.txt 2>&1

echo "Step 4: Convert to DEX (d8)..."
$BUILD_TOOLS/d8 \
    --output "$OUT/dex/" \
    --lib "$ANDROID_JAR" \
    $(find "$OUT/compiled" -name "*.class" | tr '\n' ' ')

echo "Step 5: Add DEX to APK..."
cp "$OUT/phone-bridge-unsigned.apk" "$OUT/phone-bridge-tosign.apk"
cd "$OUT/dex" && zip -r "$OUT/phone-bridge-tosign.apk" classes.dex
cd /tmp/sms-bridge-apk

echo "Step 6: Sign APK..."
if [ ! -f "$OUT/debug.keystore" ]; then
    keytool -genkey -v \
        -keystore "$OUT/debug.keystore" \
        -alias androiddebugkey \
        -keyalg RSA -keysize 2048 \
        -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=Android Debug,O=Android,C=US" 2>/dev/null
fi

$BUILD_TOOLS/apksigner sign \
    --ks "$OUT/debug.keystore" \
    --ks-pass pass:android \
    --key-pass pass:android \
    --ks-key-alias androiddebugkey \
    --out "$OUT/phone-bridge.apk" \
    "$OUT/phone-bridge-tosign.apk"

echo ""
echo "✅ Built: $OUT/phone-bridge.apk ($(du -sh $OUT/phone-bridge.apk | cut -f1))"
echo ""
echo "Install: adb install -r $OUT/phone-bridge.apk"
