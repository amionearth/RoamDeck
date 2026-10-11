# ============================================================
# setup-android.ps1
# One-shot script: add Android platform, patch manifests,
# build a debug APK.
#
# Prerequisites:
#   - Node.js 18+      https://nodejs.org
#   - JDK 17+          https://adoptium.net
#   - Android Studio   https://developer.android.com/studio
#     (installs Android SDK; set ANDROID_HOME afterwards)
#
# Run from the mobile-client directory:
#   powershell -ExecutionPolicy Bypass -File setup-android.ps1
# ============================================================

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host ""
Write-Host "=== RoamDeck Android Setup ===" -ForegroundColor Cyan
Write-Host ""

# ── Check ANDROID_HOME ───────────────────────────────────────
if (-not $env:ANDROID_HOME) {
    Write-Host "WARNING: ANDROID_HOME is not set." -ForegroundColor Yellow
    Write-Host "  Set it to your Android SDK root, e.g.:" -ForegroundColor Yellow
    Write-Host "  [System.Environment]::SetEnvironmentVariable('ANDROID_HOME','C:\Users\<you>\AppData\Local\Android\Sdk','User')" -ForegroundColor Yellow
    Write-Host "  Then restart this terminal and re-run." -ForegroundColor Yellow
    Write-Host ""
}

# ── 1. npm install ───────────────────────────────────────────
Write-Host "[1/6] Installing npm packages..." -ForegroundColor Yellow
npm install
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: npm install failed" -ForegroundColor Red
    exit 1
}

# ── 2. Add Android platform ──────────────────────────────────
Write-Host ""
Write-Host "[2/6] Adding Android platform..." -ForegroundColor Yellow
$androidDir = Join-Path $root "android"
if (Test-Path $androidDir) {
    Write-Host "      android/ already exists - skipping cap add android"
} else {
    npx cap add android
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: cap add android failed" -ForegroundColor Red
        exit 1
    }
}

# ── 3. Copy network security config ──────────────────────────
Write-Host ""
Write-Host "[3/6] Applying network security config..." -ForegroundColor Yellow
$xmlDest = Join-Path $root "android\app\src\main\res\xml"
if (-not (Test-Path $xmlDest)) {
    New-Item -ItemType Directory -Path $xmlDest | Out-Null
}
$xmlSrc = Join-Path $root "android-extras\network_security_config.xml"
$xmlOut = Join-Path $xmlDest "network_security_config.xml"
Copy-Item $xmlSrc $xmlOut -Force
Write-Host "      Copied network_security_config.xml"

# ── 4. Patch AndroidManifest.xml ─────────────────────────────
Write-Host ""
Write-Host "[4/6] Patching AndroidManifest.xml..." -ForegroundColor Yellow
$manifest = Join-Path $root "android\app\src\main\AndroidManifest.xml"

if (Test-Path $manifest) {
    $content = Get-Content $manifest -Raw

    # Add networkSecurityConfig to <application> tag if missing
    if ($content -notmatch "networkSecurityConfig") {
        $content = $content -replace '(<application)', '$1 android:networkSecurityConfig="@xml/network_security_config"'
        Write-Host "      Added networkSecurityConfig attribute"
    } else {
        Write-Host "      networkSecurityConfig already present"
    }

    # Add missing permissions before the closing </manifest> tag
    $permList = @(
        'android.permission.INTERNET',
        'android.permission.ACCESS_NETWORK_STATE',
        'android.permission.CAMERA',
        'android.permission.ACCESS_FINE_LOCATION',
        'android.permission.ACCESS_COARSE_LOCATION',
        'android.permission.READ_EXTERNAL_STORAGE',
        'android.permission.WRITE_EXTERNAL_STORAGE'
    )

    foreach ($perm in $permList) {
        if ($content -notmatch [regex]::Escape($perm)) {
            $permLine = "    <uses-permission android:name=`"$perm`" />`n</manifest>"
            $content = $content -replace '</manifest>', $permLine
            Write-Host "      Added: $perm"
        }
    }

    Set-Content -Path $manifest -Value $content -Encoding UTF8
} else {
    Write-Host "      AndroidManifest.xml not found - skipping (run again after cap add android)" -ForegroundColor Yellow
}

# ── 5. Cap sync ───────────────────────────────────────────────
Write-Host ""
Write-Host "[5/6] Running cap sync..." -ForegroundColor Yellow
npx cap sync android
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: cap sync failed" -ForegroundColor Red
    exit 1
}

# ── 6. Build debug APK ────────────────────────────────────────
Write-Host ""
Write-Host "[6/6] Building debug APK..." -ForegroundColor Yellow
$gradlew = Join-Path $root "android\gradlew.bat"

if (-not (Test-Path $gradlew)) {
    Write-Host "ERROR: gradlew.bat not found at $gradlew" -ForegroundColor Red
    Write-Host "       Make sure 'cap add android' completed successfully." -ForegroundColor Red
    exit 1
}

$androidProjectDir = Join-Path $root "android"
& $gradlew assembleDebug "-p" $androidProjectDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Gradle build failed" -ForegroundColor Red
    exit 1
}

# ── Done ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== BUILD COMPLETE ===" -ForegroundColor Green
$apkPath = Join-Path $root "android\app\build\outputs\apk\debug\app-debug.apk"

if (Test-Path $apkPath) {
    Write-Host "APK:  $apkPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Enable USB debugging on your phone"
    Write-Host "     Settings -> About Phone -> tap Build Number 7 times"
    Write-Host "     Settings -> Developer Options -> enable USB Debugging"
    Write-Host ""
    Write-Host "  2. Connect phone via USB, then run:"
    Write-Host "     adb install `"$apkPath`"" -ForegroundColor White
    Write-Host ""
    Write-Host "  If 'adb' is not recognized, add this to your PATH:"

    if ($env:ANDROID_HOME) {
        Write-Host "     $env:ANDROID_HOME\platform-tools" -ForegroundColor White
    } else {
        Write-Host "     C:\Users\<you>\AppData\Local\Android\Sdk\platform-tools" -ForegroundColor White
    }
} else {
    Write-Host "APK not found at expected path." -ForegroundColor Yellow
    Write-Host "Check: android\app\build\outputs\apk\" -ForegroundColor Yellow
}
