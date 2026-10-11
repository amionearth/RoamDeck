#!/usr/bin/env bash
# ============================================================
# setup-android.sh  (Linux / macOS version of setup-android.ps1)
# ============================================================
set -e
cd "$(dirname "$0")"

echo "=== RoamDeck Android Setup ==="

echo "[1/6] npm install…"
npm install

echo "[2/6] Adding Android platform…"
if [ -d "android" ]; then
  echo "      android/ already exists – skipping"
else
  npx cap add android
fi

echo "[3/6] Copying network security config…"
mkdir -p android/app/src/main/res/xml
cp android-extras/network_security_config.xml \
   android/app/src/main/res/xml/network_security_config.xml

echo "[4/6] Patching AndroidManifest…"
MANIFEST="android/app/src/main/AndroidManifest.xml"
if [ -f "$MANIFEST" ]; then
  # Add networkSecurityConfig if missing
  if ! grep -q "networkSecurityConfig" "$MANIFEST"; then
    sed -i 's/<application /<application android:networkSecurityConfig="@xml\/network_security_config" /g' "$MANIFEST"
  fi
fi

echo "[5/6] cap sync…"
npx cap sync android

echo "[6/6] Building debug APK…"
chmod +x android/gradlew
./android/gradlew assembleDebug -p android

APK="android/app/build/outputs/apk/debug/app-debug.apk"
echo ""
echo "=== BUILD COMPLETE ==="
[ -f "$APK" ] && echo "APK: $(realpath $APK)" || echo "Check android/app/build/outputs/apk/"
echo ""
echo "Install: adb install $APK"
