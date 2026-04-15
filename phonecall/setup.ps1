<#
.SYNOPSIS
    One-time setup script for Phone Call Controller.
    Enables USB Debugging detection, installs ADB, and tests connection.
#>

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  Phone Call Controller - Setup" -ForegroundColor White
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# -----------------------------------------------------------------
# Step 1: Check/Install ADB
# -----------------------------------------------------------------
Write-Host "  [Step 1/4] Checking for ADB..." -ForegroundColor Yellow

$adbPath = $null

# Check if already installed
try {
    $adbPath = (Get-Command adb -ErrorAction Stop).Source
    Write-Host "  [OK] ADB already installed: $adbPath" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] ADB not found. Installing..." -ForegroundColor Yellow

    # Try winget
    $installed = $false
    try {
        $null = Get-Command winget -ErrorAction Stop
        Write-Host "    Trying winget..." -ForegroundColor Gray
        $result = winget install Google.PlatformTools --accept-source-agreements --accept-package-agreements 2>&1
        $ptPath = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools'
        $env:PATH += ";$ptPath"
        $null = Get-Command adb -ErrorAction Stop
        $installed = $true
        Write-Host "  [OK] ADB installed via winget" -ForegroundColor Green
    } catch {}

    if (-not $installed) {
        # Download directly
        Write-Host "    Downloading ADB platform-tools..." -ForegroundColor Gray
        $adbDir = Join-Path $PSScriptRoot "platform-tools"
        $zipPath = Join-Path $PSScriptRoot "platform-tools.zip"

        if (-not (Test-Path $adbDir)) {
            try {
                $url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
                Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
                Expand-Archive -Path $zipPath -DestinationPath $PSScriptRoot -Force
                Remove-Item $zipPath -Force
                $env:PATH = "$adbDir;" + $env:PATH
                Write-Host "  [OK] ADB downloaded to: $adbDir" -ForegroundColor Green
                $installed = $true
            } catch {
                Write-Host "  [FAIL] Failed to download ADB: $_" -ForegroundColor Red
                Write-Host ""
                Write-Host "  Please manually download from:" -ForegroundColor Yellow
                Write-Host "  https://developer.android.com/tools/releases/platform-tools" -ForegroundColor Cyan
                exit 1
            }
        } else {
            $env:PATH = "$adbDir;" + $env:PATH
            $installed = $true
            Write-Host "  [OK] ADB found in local folder" -ForegroundColor Green
        }
    }
}

# -----------------------------------------------------------------
# Step 2: Phone setup instructions
# -----------------------------------------------------------------
Write-Host ""
Write-Host "  [Step 2/4] Phone Setup Required" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Please do the following on your Android phone:" -ForegroundColor White
Write-Host "  -----------------------------------------------" -ForegroundColor DarkGray
Write-Host '  1. Go to Settings -> About Phone' -ForegroundColor Gray
Write-Host "  2. Tap 'Build Number' 7 times rapidly" -ForegroundColor Gray
Write-Host "     (You will see 'You are now a developer!')" -ForegroundColor DarkGray
Write-Host '  3. Go back to Settings -> Developer Options' -ForegroundColor Gray
Write-Host "  4. Enable 'USB Debugging'" -ForegroundColor Gray
Write-Host "  5. Connect your phone to laptop with USB cable" -ForegroundColor Gray
Write-Host "  6. Accept the 'Allow USB debugging?' popup on phone" -ForegroundColor Gray
Write-Host "     (Check 'Always allow from this computer')" -ForegroundColor DarkGray
Write-Host ""

Read-Host "  Press Enter when your phone is connected via USB"

# -----------------------------------------------------------------
# Step 3: Verify connection
# -----------------------------------------------------------------
Write-Host ""
Write-Host "  [Step 3/4] Testing connection..." -ForegroundColor Yellow

adb start-server 2>&1 | Out-Null
Start-Sleep -Seconds 2

$output = adb devices 2>&1 | Out-String
$devices = @()
foreach ($line in ($output -split "`n")) {
    if ($line -match "^(.+?)\s+(device|unauthorized|offline)") {
        $devices += [PSCustomObject]@{
            Id     = $Matches[1].Trim()
            Status = $Matches[2].Trim()
        }
    }
}

if (-not $devices) {
    Write-Host "  [FAIL] No devices detected!" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Troubleshooting:" -ForegroundColor Yellow
    Write-Host "   - Make sure USB cable supports data (not charge-only)" -ForegroundColor Gray
    Write-Host "   - Try a different USB port" -ForegroundColor Gray
    Write-Host "   - Check phone for 'Allow USB debugging' popup" -ForegroundColor Gray
    Write-Host '   - Try: Settings -> Developer Options -> Revoke USB debugging' -ForegroundColor Gray
    Write-Host "     then re-enable USB Debugging and reconnect" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

$authorized = $devices | Where-Object { $_.Status -eq "device" }
$unauthorized = $devices | Where-Object { $_.Status -eq "unauthorized" }

if ($unauthorized) {
    Write-Host "  [WARN] Device found but UNAUTHORIZED" -ForegroundColor Yellow
    Write-Host "    Check your phone - accept the 'Allow USB debugging' popup" -ForegroundColor Gray
    Write-Host ""
    Read-Host "  Press Enter after accepting the popup"

    # Re-check
    Start-Sleep -Seconds 1
    $output = adb devices 2>&1 | Out-String
    $authorized = @()
    foreach ($line in ($output -split "`n")) {
        if ($line -match "^(.+?)\s+device$") {
            $authorized += $Matches[1].Trim()
        }
    }
}

if (-not $authorized) {
    Write-Host "  [FAIL] Device still not authorized. Please retry setup." -ForegroundColor Red
    exit 1
}

Write-Host "  [OK] Phone connected successfully!" -ForegroundColor Green

# Show phone info
try {
    $modelRaw = adb shell getprop ro.product.model 2>&1 | Out-String
    $brandRaw = adb shell getprop ro.product.brand 2>&1 | Out-String
    $androidRaw = adb shell getprop ro.build.version.release 2>&1 | Out-String
    $carrierRaw = adb shell getprop gsm.operator.alpha 2>&1 | Out-String
    $model = if ($modelRaw) { $modelRaw.Trim() } else { 'N/A' }
    $brand = if ($brandRaw) { $brandRaw.Trim() } else { 'N/A' }
    $android = if ($androidRaw) { $androidRaw.Trim() } else { 'N/A' }
    $carrier = if ($carrierRaw) { $carrierRaw.Trim() } else { 'N/A' }
} catch {
    $model = 'N/A'; $brand = 'N/A'; $android = 'N/A'; $carrier = 'N/A'
}

Write-Host ""
Write-Host "  Phone Details:" -ForegroundColor White
Write-Host "   Model:   $brand $model" -ForegroundColor Gray
Write-Host "   Android: $android" -ForegroundColor Gray
Write-Host "   Carrier: $carrier" -ForegroundColor Gray

# -----------------------------------------------------------------
# Step 4: Test call capability
# -----------------------------------------------------------------
Write-Host ""
Write-Host "  [Step 4/4] Testing call capability..." -ForegroundColor Yellow

try {
    $state = adb shell "dumpsys telephony.registry" 2>$null | Select-String "mCallState"
    if ($state) {
        Write-Host "  [OK] Telephony access confirmed" -ForegroundColor Green
    }

    # Test dialer intent (won't actually call)
    adb shell "am start -a android.intent.action.DIAL" 2>&1 | Out-Null
    Write-Host "  [OK] Phone dialer accessible" -ForegroundColor Green
    Start-Sleep -Seconds 1
    adb shell "input keyevent KEYCODE_HOME" 2>&1 | Out-Null

} catch {
    Write-Host "  [WARN] Some phone features may be restricted: $_" -ForegroundColor Yellow
}

# -----------------------------------------------------------------
# Setup WiFi ADB (optional)
# -----------------------------------------------------------------
Write-Host ""
Write-Host "  -----------------------------------------------" -ForegroundColor DarkGray
$wifiSetup = Read-Host "  Would you like to set up WiFi ADB (wireless)? [y/N]"

if ($wifiSetup -match "^[Yy]") {
    Write-Host ""
    Write-Host "  Setting up WiFi ADB..." -ForegroundColor Yellow

    # Get phone IP
    $ipRouteRaw = adb shell "ip route" 2>&1 | Out-String
    $phoneIp = $null
    if ($ipRouteRaw -match 'src (\d+\.\d+\.\d+\.\d+)') {
        $phoneIp = $Matches[1]
    }

    if ($phoneIp) {
        Write-Host "  Phone IP detected: $phoneIp" -ForegroundColor Gray

        adb tcpip 5555 2>&1 | Out-Null
        Write-Host "  [OK] TCP/IP mode enabled on port 5555" -ForegroundColor Green

        Start-Sleep -Seconds 2
        $connStr = "${phoneIp}:5555"
        $result = adb connect $connStr 2>&1 | Out-String

        if ($result -match "connected|already") {
            Write-Host "  [OK] WiFi ADB connected! You can now unplug the USB cable." -ForegroundColor Green
            Write-Host ""
            Write-Host "  To reconnect wirelessly later, run:" -ForegroundColor White
            Write-Host "    .\call.ps1 -Phone $phoneIp" -ForegroundColor Cyan
        } else {
            Write-Host "  [WARN] Could not connect wirelessly. USB connection still works." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [WARN] Could not detect phone IP. Check WiFi connection." -ForegroundColor Yellow
        Write-Host '  Find it manually: Settings -> WiFi -> tap your network -> IP Address' -ForegroundColor Gray
    }
}

# -----------------------------------------------------------------
# Done
# -----------------------------------------------------------------
Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host "  [OK] Setup Complete!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  To start making calls:" -ForegroundColor White
Write-Host "    .\call.ps1                    # Interactive menu" -ForegroundColor Cyan
Write-Host "    .\call.ps1 -Dial '+1234567890'  # Quick dial" -ForegroundColor Cyan
Write-Host "    .\call.ps1 -Status            # Check phone status" -ForegroundColor Cyan
Write-Host ""
