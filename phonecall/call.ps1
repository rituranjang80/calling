<#
.SYNOPSIS
    Phone Call Controller - Make GSM/cellular calls from your laptop via Android phone (ADB)

.DESCRIPTION
    Connects to your Android phone over USB or WiFi using ADB, and provides
    an interactive menu to dial numbers, answer calls, hang up, send SMS,
    and manage the connection.

.NOTES
    Requirements:
      - Android phone with USB Debugging enabled
      - ADB installed (this script can install it via scoop/winget)
      - Phone connected via USB cable or WiFi (same network)

.EXAMPLE
    .\call.ps1
    .\call.ps1 -Phone "192.168.1.100"
    .\call.ps1 -Dial "+1234567890"
#>

param(
    [string]$Phone,       # WiFi IP of Android phone (optional, uses USB by default)
    [string]$Dial,        # Directly dial a number without interactive menu
    [switch]$Disconnect,  # Disconnect WiFi ADB
    [switch]$Status       # Show connection status only
)

# -----------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------
$ErrorActionPreference = "Stop"
$ContactsFile = Join-Path $PSScriptRoot "contacts.json"
$CallLogFile = Join-Path $PSScriptRoot "call_log.csv"
$Script:AdbSerial = $null  # Will be set after device detection
$Script:ScrcpyProcess = $null
$Script:AudioActive = $false

# Helper output functions
function Write-Header { param($msg) Write-Host "`n===========================================" -ForegroundColor Cyan; Write-Host "  $msg" -ForegroundColor White; Write-Host "===========================================" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Write-Info { param($msg) Write-Host "  [i] $msg" -ForegroundColor Cyan }

# Run adb command targeting the selected device
function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments=$true)]$Arguments)
    if ($Script:AdbSerial) {
        & adb -s $Script:AdbSerial @Arguments
    } else {
        & adb @Arguments
    }
}

# -----------------------------------------------------------------
# ADB Helper Functions
# -----------------------------------------------------------------

function Test-AdbInstalled {
    try {
        $null = Get-Command adb -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Install-Adb {
    Write-Warn "ADB not found. Attempting to install..."

    # Try winget first
    try {
        $null = Get-Command winget -ErrorAction Stop
        Write-Info "Installing via winget..."
        winget install Google.PlatformTools --accept-source-agreements --accept-package-agreements 2>$null
        # Add to PATH for this session
        $ptPath = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools'
        $env:PATH += ";$ptPath"
        if (Test-AdbInstalled) { Write-Success "ADB installed via winget"; return $true }
    } catch {}

    # Try scoop
    try {
        $null = Get-Command scoop -ErrorAction Stop
        Write-Info "Installing via scoop..."
        scoop install adb 2>$null
        if (Test-AdbInstalled) { Write-Success "ADB installed via scoop"; return $true }
    } catch {}

    # Try choco
    try {
        $null = Get-Command choco -ErrorAction Stop
        Write-Info "Installing via chocolatey..."
        choco install adb -y 2>$null
        if (Test-AdbInstalled) { Write-Success "ADB installed via chocolatey"; return $true }
    } catch {}

    # Manual download
    Write-Warn "No package manager could install ADB automatically."
    Write-Info "Downloading ADB platform-tools directly..."

    $adbDir = Join-Path $PSScriptRoot "platform-tools"
    $zipPath = Join-Path $PSScriptRoot "platform-tools.zip"

    if (-not (Test-Path $adbDir)) {
        $url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
        Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
        Expand-Archive -Path $zipPath -DestinationPath $PSScriptRoot -Force
        Remove-Item $zipPath -Force
    }

    $env:PATH = "$adbDir;" + $env:PATH
    if (Test-AdbInstalled) { Write-Success "ADB downloaded to $adbDir"; return $true }

    Write-Err "Failed to install ADB. Please install manually from:"
    Write-Info "https://developer.android.com/tools/releases/platform-tools"
    return $false
}

function Get-AdbDevices {
    $output = adb devices 2>&1 | Out-String
    $devices = @()
    foreach ($line in ($output -split "`n")) {
        $line = $line.Trim()
        # Skip header and empty lines
        if ($line -eq '' -or $line -match '^List of' -or $line -match '^\*') { continue }
        if ($line -match "^(\S+)\s+(device|unauthorized|offline)$") {
            $devices += [PSCustomObject]@{
                Id     = $Matches[1].Trim()
                Status = $Matches[2].Trim()
            }
        }
    }
    return $devices
}

function Connect-PhoneWifi {
    param([string]$IpAddress, [int]$Port = 5555)

    Write-Info "Connecting to phone at ${IpAddress}:${Port}..."

    # First, if connected via USB, set up tcpip mode
    $usbDevices = Get-AdbDevices | Where-Object { $_.Id -notmatch ":" -and $_.Status -eq "device" }
    if ($usbDevices) {
        Write-Info "Setting phone to TCP/IP mode via USB..."
        adb tcpip $Port 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    }

    $connStr = "${IpAddress}:${Port}"
    $result = adb connect $connStr 2>&1 | Out-String
    if ($result -match "connected|already") {
        Write-Success "Connected to ${IpAddress}:${Port}"
        return $true
    } else {
        Write-Err "Failed to connect: $result"
        return $false
    }
}

function Disconnect-PhoneWifi {
    adb disconnect 2>&1 | Out-Null
    Write-Success "Disconnected all WiFi ADB connections"
}

function Get-PhoneInfo {
    $info = @{}
    try {
        $raw = Invoke-Adb shell getprop ro.product.model 2>&1 | Out-String
        $info.Model = if ($raw) { $raw.Trim() } else { 'N/A' }
        $raw = Invoke-Adb shell getprop ro.product.brand 2>&1 | Out-String
        $info.Brand = if ($raw) { $raw.Trim() } else { 'N/A' }
        $raw = Invoke-Adb shell getprop ro.build.version.release 2>&1 | Out-String
        $info.Android = if ($raw) { $raw.Trim() } else { 'N/A' }
        $raw = Invoke-Adb shell dumpsys battery 2>&1 | Out-String
        if ($raw -match 'level:\s*(\d+)') { $info.Battery = $Matches[1] } else { $info.Battery = '?' }
        $raw = Invoke-Adb shell getprop gsm.operator.alpha 2>&1 | Out-String
        $info.Carrier = if ($raw) { $raw.Trim() } else { 'N/A' }
    } catch {
        $info.Error = $_.Exception.Message
    }
    return $info
}

# -----------------------------------------------------------------
# Scrcpy Audio Forwarding (laptop speaker/mic for calls)
# -----------------------------------------------------------------

function Get-ScrcpyPath {
    $localScrcpy = Join-Path $PSScriptRoot "scrcpy\scrcpy.exe"
    if (Test-Path $localScrcpy) { return $localScrcpy }
    try {
        return (Get-Command scrcpy -ErrorAction Stop).Source
    } catch {
        return $null
    }
}

function Install-Scrcpy {
    Write-Info "Downloading scrcpy for laptop audio forwarding..."
    $scrcpyDir = Join-Path $PSScriptRoot "scrcpy"
    $zipPath = Join-Path $PSScriptRoot "scrcpy.zip"

    if (-not (Test-Path $scrcpyDir)) {
        $url = "https://github.com/Genymobile/scrcpy/releases/download/v3.1/scrcpy-win64-v3.1.zip"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
            Expand-Archive -Path $zipPath -DestinationPath $PSScriptRoot -Force
            # Rename extracted folder to 'scrcpy'
            $extracted = Get-ChildItem $PSScriptRoot -Directory -Filter "scrcpy-win64*" | Select-Object -First 1
            if ($extracted) {
                if (Test-Path $scrcpyDir) { Remove-Item $scrcpyDir -Recurse -Force }
                Move-Item $extracted.FullName $scrcpyDir -Force
            }
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
            Write-Success "scrcpy downloaded to $scrcpyDir"
            return $true
        } catch {
            Write-Err "Failed to download scrcpy: $_"
            Write-Info "You can manually download from: https://github.com/Genymobile/scrcpy/releases"
            return $false
        }
    }
    return $true
}

function Start-AudioForwarding {
    if ($Script:AudioActive -and $Script:ScrcpyProcess -and -not $Script:ScrcpyProcess.HasExited) {
        Write-Warn "Audio forwarding already active"
        return $true
    }

    $scrcpyExe = Get-ScrcpyPath
    if (-not $scrcpyExe) {
        if (-not (Install-Scrcpy)) {
            Write-Err "Cannot start audio forwarding without scrcpy"
            return $false
        }
        $scrcpyExe = Get-ScrcpyPath
    }

    if (-not $scrcpyExe) {
        Write-Err "scrcpy not found after installation attempt"
        return $false
    }

    Write-Info "Starting audio forwarding to laptop speakers..."

    # Build arguments: no video, forward phone audio output to laptop
    $scrcpyArgs = @("--no-video", "--audio-source=output")
    if ($Script:AdbSerial) {
        $scrcpyArgs += @("--serial", $Script:AdbSerial)
    }

    try {
        # -WindowStyle not supported on Linux/PowerShell Core; use -RedirectStandardOutput
        $startArgs = @{
            FilePath = $scrcpyExe
            ArgumentList = $scrcpyArgs
            PassThru = $true
        }
        if ($PSVersionTable.PSEdition -ne 'Core') {
            $startArgs.WindowStyle = 'Hidden'
        } else {
            $nullOut = Join-Path ([System.IO.Path]::GetTempPath()) "scrcpy_out.log"
            $nullErr = Join-Path ([System.IO.Path]::GetTempPath()) "scrcpy_err.log"
            $startArgs.RedirectStandardOutput = $nullOut
            $startArgs.RedirectStandardError = $nullErr
        }
        $Script:ScrcpyProcess = Start-Process @startArgs
        Start-Sleep -Seconds 3

        if ($Script:ScrcpyProcess -and -not $Script:ScrcpyProcess.HasExited) {
            $Script:AudioActive = $true
            Write-Success "Laptop speaker active - call audio plays through laptop!"
            Write-Info "Talk through your phone mic (phone is nearby) or use Bluetooth earpiece"
            return $true
        } else {
            Write-Warn "scrcpy audio may not be supported on this device"
            Write-Info "Fallback: Putting phone on speakerphone..."
            Invoke-Adb shell "input keyevent KEYCODE_SPEAKER" 2>&1 | Out-Null
            return $false
        }
    } catch {
        Write-Err "Failed to start audio forwarding: $_"
        return $false
    }
}

function Stop-AudioForwarding {
    if ($Script:ScrcpyProcess -and -not $Script:ScrcpyProcess.HasExited) {
        try { $Script:ScrcpyProcess.Kill() } catch {}
        $Script:ScrcpyProcess = $null
    }
    $Script:AudioActive = $false
}

# -----------------------------------------------------------------
# Call Functions
# -----------------------------------------------------------------

function Start-PhoneCall {
    param([string]$Number)

    # Clean the number
    $Number = $Number -replace "[^\d+*#]", ""

    if ([string]::IsNullOrWhiteSpace($Number)) {
        Write-Err "Invalid phone number"
        return
    }

    Write-Info "Dialing $Number ..."

    # Use am start to open the dialer and call
    $encoded = [Uri]::EscapeDataString($Number)
    Invoke-Adb shell "am start -a android.intent.action.CALL -d tel:$encoded" 2>&1 | Out-Null

    Write-Success "Call initiated to $Number"

    # Auto-start laptop speaker audio forwarding
    $audioOk = Start-AudioForwarding
    if ($audioOk) {
        Write-Info "Call audio -> laptop speakers. Speak into phone mic."
    } else {
        Write-Info "Using phone speaker. Keep phone nearby."
    }

    # Log the call
    Log-Call -Number $Number -Type "Outgoing"
}

function Stop-PhoneCall {
    Write-Info "Ending call..."

    # Stop audio forwarding
    Stop-AudioForwarding

    # Send KEYCODE_ENDCALL
    Invoke-Adb shell "input keyevent KEYCODE_ENDCALL" 2>&1 | Out-Null

    Write-Success "Call ended"
}

function Answer-PhoneCall {
    Write-Info "Answering incoming call..."

    # Send KEYCODE_CALL to answer
    Invoke-Adb shell "input keyevent KEYCODE_CALL" 2>&1 | Out-Null

    Write-Success "Call answered"
}

function Set-Speakerphone {
    param([bool]$Enable)

    if ($Enable) {
        Invoke-Adb shell "input keyevent KEYCODE_VOLUME_UP" 2>&1 | Out-Null
        Write-Info "Tip: Enable speaker on your phone for hands-free talking, or use Bluetooth"
    }
}

function Send-Sms {
    param([string]$Number, [string]$Message)

    $Number = $Number -replace "[^\d+*#]", ""
    if ([string]::IsNullOrWhiteSpace($Number) -or [string]::IsNullOrWhiteSpace($Message)) {
        Write-Err "Number and message are required"
        return
    }

    Write-Info "Sending SMS to $Number ..."

    # Use am start to send SMS via intent
    $encodedMsg = $Message -replace "'", "'\''"
    Invoke-Adb shell "am start -a android.intent.action.SENDTO -d sms:$Number --es sms_body '$encodedMsg' --ez exit_on_sent true" 2>&1 | Out-Null

    # SMS app will open with message pre-filled; user needs to press Send on phone
    Start-Sleep -Seconds 2

    Write-Success "SMS app opened with message. Press Send on your phone."
    Log-Call -Number $Number -Type "SMS"
}

function Open-Dialer {
    param([string]$Number = "")

    if ($Number) {
        $encoded = [Uri]::EscapeDataString($Number)
        Invoke-Adb shell "am start -a android.intent.action.DIAL -d tel:$encoded" 2>&1 | Out-Null
    } else {
        Invoke-Adb shell "am start -a android.intent.action.DIAL" 2>&1 | Out-Null
    }
    Write-Success "Dialer opened on phone"
}

function Get-CallState {
    try {
        $raw = Invoke-Adb shell "dumpsys telephony.registry" 2>&1 | Out-String
        if ($raw -match "mCallState=(\d+)") {
            switch ($Matches[1]) {
                "0" { return "Idle" }
                "1" { return "Ringing" }
                "2" { return "In Call" }
                default { return "Unknown" }
            }
        }
    } catch {}
    return "Unknown"
}

# -----------------------------------------------------------------
# Contacts
# -----------------------------------------------------------------

function Get-Contacts {
    if (Test-Path $ContactsFile) {
        return Get-Content $ContactsFile -Raw | ConvertFrom-Json
    }
    return @()
}

function Add-Contact {
    param([string]$Name, [string]$Number)

    $contacts = @(Get-Contacts)
    $contacts += [PSCustomObject]@{ Name = $Name; Number = $Number }
    $contacts | ConvertTo-Json -Depth 5 | Set-Content $ContactsFile -Encoding UTF8
    Write-Success "Contact '$Name' ($Number) saved"
}

function Show-Contacts {
    $contacts = @(Get-Contacts)
    if ($contacts.Count -eq 0) {
        Write-Warn "No saved contacts. Use option [6] to add contacts."
        return $null
    }

    Write-Host ""
    Write-Host "  Saved Contacts:" -ForegroundColor Yellow
    Write-Host "  -----------------------------------" -ForegroundColor DarkGray
    for ($i = 0; $i -lt $contacts.Count; $i++) {
        Write-Host "   [$($i+1)] $($contacts[$i].Name)" -ForegroundColor White -NoNewline
        Write-Host " - $($contacts[$i].Number)" -ForegroundColor Gray
    }
    Write-Host ""
    return $contacts
}

function Get-PhoneContacts {
    Write-Info "Fetching contacts from phone..."
    try {
        $raw = Invoke-Adb shell "content query --uri content://contacts/phones/ --projection display_name:number" 2>$null
        if ($raw) {
            $raw -split "`n" | ForEach-Object {
                if ($_ -match "display_name=(.+?),\s*number=(.+)") {
                    Write-Host "   $($Matches[1].Trim())" -ForegroundColor White -NoNewline
                    Write-Host " - $($Matches[2].Trim())" -ForegroundColor Gray
                }
            }
        } else {
            Write-Warn "Could not read contacts (permission may be needed)"
        }
    } catch {
        Write-Warn "Failed to read phone contacts: $_"
    }
}

# -----------------------------------------------------------------
# Call Log
# -----------------------------------------------------------------

function Log-Call {
    param([string]$Number, [string]$Type)

    $entry = [PSCustomObject]@{
        Timestamp = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        Number    = $Number
        Type      = $Type
    }

    if (-not (Test-Path $CallLogFile)) {
        "Timestamp,Number,Type" | Set-Content $CallLogFile
    }
    "$($entry.Timestamp),$($entry.Number),$($entry.Type)" | Add-Content $CallLogFile
}

function Show-CallLog {
    if (Test-Path $CallLogFile) {
        $log = Import-Csv $CallLogFile | Select-Object -Last 20
        if ($log) {
            Write-Host ""
            Write-Host "  Recent Call Log:" -ForegroundColor Yellow
            Write-Host "  ---------------------------------------------------" -ForegroundColor DarkGray
            foreach ($entry in $log) {
                $icon = switch ($entry.Type) { "Outgoing" { "OUT->" } "Incoming" { "IN<-" } "SMS" { "SMS " } default { "CALL" } }
                Write-Host "   $icon $($entry.Timestamp)  $($entry.Number)  [$($entry.Type)]" -ForegroundColor Gray
            }
        }
    } else {
        Write-Warn "No call log yet"
    }
}

# -----------------------------------------------------------------
# DTMF Tones (during call)
# -----------------------------------------------------------------

function Send-Dtmf {
    param([string]$Digit)

    $keyMap = @{
        "0" = "KEYCODE_0"; "1" = "KEYCODE_1"; "2" = "KEYCODE_2"
        "3" = "KEYCODE_3"; "4" = "KEYCODE_4"; "5" = "KEYCODE_5"
        "6" = "KEYCODE_6"; "7" = "KEYCODE_7"; "8" = "KEYCODE_8"
        "9" = "KEYCODE_9"; "*" = "KEYCODE_STAR"; "#" = "KEYCODE_POUND"
    }

    if ($keyMap.ContainsKey($Digit)) {
        Invoke-Adb shell "input keyevent $($keyMap[$Digit])" 2>&1 | Out-Null
        Write-Host $Digit -NoNewline -ForegroundColor Yellow
    }
}

# -----------------------------------------------------------------
# In-Call Menu
# -----------------------------------------------------------------

function Show-InCallMenu {
    param([string]$Number)

    Write-Host ""
    Write-Host "  +======================================+" -ForegroundColor Green
    Write-Host "  |      IN CALL: $($Number.PadRight(23))|" -ForegroundColor Green
    Write-Host "  +======================================+" -ForegroundColor Green
    Write-Host "  |  [H] Hang Up                        |" -ForegroundColor White
    Write-Host "  |  [M] Mute/Unmute                    |" -ForegroundColor White
    Write-Host "  |  [S] Speaker Toggle                 |" -ForegroundColor White
    Write-Host "  |  [A] Laptop Audio On/Off             |" -ForegroundColor White
    Write-Host "  |  [D] Send DTMF Tones (IVR menus)   |" -ForegroundColor White
    Write-Host "  |  [B] Back to main menu (call stays) |" -ForegroundColor White
    $audioStatus = if ($Script:AudioActive) { "ON - Laptop Speaker" } else { "OFF - Phone Speaker" }
    Write-Host "  |  Audio: $($audioStatus.PadRight(28))|" -ForegroundColor Yellow
    Write-Host "  +======================================+" -ForegroundColor Green

    # Give the call a moment to connect before checking state
    Start-Sleep -Seconds 3

    while ($true) {
        $callState = Get-CallState
        if ($callState -eq "Idle") {
            Write-Info "Call ended (remote party hung up or call was rejected)"
            break
        }

        $key = Read-Host "`n  In-Call"
        switch ($key.ToUpper()) {
            "H" { Stop-PhoneCall; break }
            "M" { Invoke-Adb shell "input keyevent KEYCODE_MUTE" 2>&1 | Out-Null; Write-Info "Mute toggled" }
            "S" { Invoke-Adb shell "input keyevent KEYCODE_VOLUME_UP" 2>&1 | Out-Null; Write-Info "Volume up" }
            "A" {
                if ($Script:AudioActive) {
                    Stop-AudioForwarding
                    Write-Success "Laptop audio OFF - using phone speaker"
                } else {
                    $ok = Start-AudioForwarding
                    if (-not $ok) { Write-Warn "Could not start laptop audio" }
                }
            }
            "D" {
                $tones = Read-Host "  Enter digits to send (e.g. 1234#)"
                foreach ($char in $tones.ToCharArray()) {
                    Send-Dtmf -Digit ([string]$char)
                    Start-Sleep -Milliseconds 200
                }
                Write-Host ""
            }
            "B" { Write-Info "Returning to main menu (call continues on phone)"; return }
            default { Write-Warn "Unknown command" }
        }

        if ($key.ToUpper() -eq "H") { break }
    }
}

# -----------------------------------------------------------------
# Main Interactive Menu
# -----------------------------------------------------------------

function Show-MainMenu {
    $phoneInfo = Get-PhoneInfo
    $callState = Get-CallState

    Clear-Host
    Write-Host ""
    Write-Host "  +==============================================+" -ForegroundColor Cyan
    Write-Host "  |         PHONE CALL CONTROLLER                |" -ForegroundColor Cyan
    Write-Host "  |         Call from your Laptop via ADB        |" -ForegroundColor Cyan
    Write-Host "  +==============================================+" -ForegroundColor Cyan

    if ($phoneInfo.Model) {
        $phoneLine = "  Phone: $($phoneInfo.Brand) $($phoneInfo.Model)"
        Write-Host $phoneLine -ForegroundColor Gray
        $battLine = "  Android: $($phoneInfo.Android)  Battery: $($phoneInfo.Battery)%"
        Write-Host $battLine -ForegroundColor Gray
        Write-Host "  Carrier: $($phoneInfo.Carrier)" -ForegroundColor Gray
    }

    $stateColor = switch ($callState) { "In Call" { "Green" } "Ringing" { "Yellow" } default { "Gray" } }
    Write-Host "  Call State: $callState" -ForegroundColor $stateColor

    Write-Host "  +----------------------------------------------+" -ForegroundColor Cyan
    Write-Host "  |                                              |" -ForegroundColor Cyan
    Write-Host "  |   [1] Dial a Number                         |" -ForegroundColor White
    Write-Host "  |   [2] Call from Contacts                    |" -ForegroundColor White
    Write-Host "  |   [3] Answer Incoming Call                  |" -ForegroundColor White
    Write-Host "  |   [4] Hang Up / End Call                    |" -ForegroundColor White
    Write-Host "  |   [5] Send SMS                              |" -ForegroundColor White
    Write-Host "  |   [6] Manage Contacts                       |" -ForegroundColor White
    Write-Host "  |   [7] View Call Log                         |" -ForegroundColor White
    Write-Host "  |   [8] Open Phone Dialer                     |" -ForegroundColor White
    Write-Host "  |   [9] WiFi ADB Connect/Disconnect           |" -ForegroundColor White
    Write-Host "  |   [0] Exit                                  |" -ForegroundColor White
    Write-Host "  |                                              |" -ForegroundColor Cyan
    Write-Host "  +==============================================+" -ForegroundColor Cyan
    Write-Host ""
}

function Start-InteractiveMenu {
    while ($true) {
        Show-MainMenu
        $choice = Read-Host "  Select option"

        switch ($choice) {
            "1" {
                # Dial a number
                $number = Read-Host "`n  Enter phone number (e.g. +1234567890)"
                if ($number) {
                    Start-PhoneCall -Number $number
                    Show-InCallMenu -Number $number
                }
            }
            "2" {
                # Call from contacts
                $contacts = Show-Contacts
                if ($contacts) {
                    $sel = Read-Host "  Select contact number"
                    $idx = [int]$sel - 1
                    if ($idx -ge 0 -and $idx -lt $contacts.Count) {
                        $contact = $contacts[$idx]
                        Start-PhoneCall -Number $contact.Number
                        Show-InCallMenu -Number "$($contact.Name) ($($contact.Number))"
                    } else {
                        Write-Err "Invalid selection"
                    }
                }
                Read-Host "`n  Press Enter to continue"
            }
            "3" {
                # Answer incoming call
                $state = Get-CallState
                if ($state -eq "Ringing") {
                    Answer-PhoneCall
                    Show-InCallMenu -Number "Incoming"
                } else {
                    Write-Warn "No incoming call detected (state: $state)"
                    Write-Info "Tip: If your phone is ringing but not detected, try answering manually"
                    Read-Host "`n  Press Enter to continue"
                }
            }
            "4" {
                # Hang up
                Stop-PhoneCall
                Read-Host "`n  Press Enter to continue"
            }
            "5" {
                # Send SMS
                $number = Read-Host "`n  Enter phone number"
                $message = Read-Host "  Enter message"
                if ($number -and $message) {
                    Send-Sms -Number $number -Message $message
                }
                Read-Host "`n  Press Enter to continue"
            }
            "6" {
                # Manage contacts
                Write-Host "`n  Contact Management:" -ForegroundColor Yellow
                Write-Host "   [A] Add contact"
                Write-Host "   [L] List saved contacts"
                Write-Host "   [P] Show phone contacts"
                $sub = Read-Host "  Select"

                switch ($sub.ToUpper()) {
                    "A" {
                        $name = Read-Host "  Contact name"
                        $num = Read-Host "  Phone number"
                        if ($name -and $num) { Add-Contact -Name $name -Number $num }
                    }
                    "L" { Show-Contacts | Out-Null }
                    "P" { Get-PhoneContacts }
                }
                Read-Host "`n  Press Enter to continue"
            }
            "7" {
                # Call log
                Show-CallLog
                Read-Host "`n  Press Enter to continue"
            }
            "8" {
                # Open dialer
                $num = Read-Host "`n  Pre-fill number (or Enter for blank)"
                Open-Dialer -Number $num
                Read-Host "`n  Press Enter to continue"
            }
            "9" {
                # WiFi ADB
                Write-Host "`n  WiFi ADB:" -ForegroundColor Yellow
                Write-Host "   [C] Connect to phone via WiFi"
                Write-Host "   [D] Disconnect WiFi"
                Write-Host "   [S] Show connected devices"
                $sub = Read-Host "  Select"

                switch ($sub.ToUpper()) {
                    "C" {
                        $ip = Read-Host "  Enter phone IP address (e.g. 192.168.1.100)"
                        if ($ip) { Connect-PhoneWifi -IpAddress $ip }
                    }
                    "D" { Disconnect-PhoneWifi }
                    "S" {
                        $devices = Get-AdbDevices
                        if ($devices) {
                            Write-Host ""
                            foreach ($d in $devices) {
                                $statusColor = if ($d.Status -eq "device") { "Green" } else { "Red" }
                                Write-Host "   $($d.Id)" -ForegroundColor White -NoNewline
                                Write-Host " [$($d.Status)]" -ForegroundColor $statusColor
                            }
                        } else {
                            Write-Warn "No devices connected"
                        }
                    }
                }
                Read-Host "`n  Press Enter to continue"
            }
            "0" {
                Write-Host "`n  Goodbye!`n" -ForegroundColor Cyan
                return
            }
            default {
                Write-Warn "Invalid option"
                Start-Sleep -Seconds 1
            }
        }
    }
}

# -----------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------

Write-Header "Phone Call Controller - Starting"

# Check ADB
if (-not (Test-AdbInstalled)) {
    if (-not (Install-Adb)) {
        exit 1
    }
}
Write-Success "ADB found: $(Get-Command adb | Select-Object -ExpandProperty Source)"

# Start ADB server
adb start-server 2>&1 | Out-Null

# Handle WiFi connection if specified
if ($Phone) {
    Connect-PhoneWifi -IpAddress $Phone
}

# Handle disconnection
if ($Disconnect) {
    Disconnect-PhoneWifi
    exit 0
}

# Check for connected device
$devices = Get-AdbDevices
if (-not $devices -or ($devices | Where-Object { $_.Status -eq "device" }).Count -eq 0) {
    Write-Err "No Android device connected!"
    Write-Host ""
    Write-Info "To connect your phone:"
    Write-Host '   1. Enable Developer Options on your Android phone:' -ForegroundColor Gray
    Write-Host '      Settings -> About Phone -> Tap Build Number 7 times' -ForegroundColor DarkGray
    Write-Host '   2. Enable USB Debugging:' -ForegroundColor Gray
    Write-Host '      Settings -> Developer Options -> USB Debugging = ON' -ForegroundColor DarkGray
    Write-Host '   3. Connect phone to laptop via USB cable' -ForegroundColor Gray
    Write-Host '   4. Accept the Allow USB Debugging prompt on your phone' -ForegroundColor Gray
    Write-Host ""
    Write-Info "For wireless (WiFi) connection:"
    Write-Host '   1. First connect via USB and run: .\call.ps1' -ForegroundColor Gray
    Write-Host '   2. Then disconnect USB and run: .\call.ps1 -Phone YOUR_PHONE_IP' -ForegroundColor Gray
    Write-Host '   3. Find your phone IP: Settings -> WiFi -> tap network -> IP Address' -ForegroundColor Gray
    Write-Host ""
    exit 1
}

# Pick the best device: prefer USB over WiFi
$readyDevices = @($devices | Where-Object { $_.Status -eq "device" })
if ($readyDevices.Count -gt 1) {
    # Prefer USB device (ID without colon) over WiFi (IP:port)
    $usbDev = $readyDevices | Where-Object { $_.Id -notmatch ':' } | Select-Object -First 1
    if ($usbDev) {
        $connectedDevice = $usbDev
    } else {
        $connectedDevice = $readyDevices[0]
    }
    Write-Warn "Multiple devices found ($($readyDevices.Count)). Using: $($connectedDevice.Id)"
} else {
    $connectedDevice = $readyDevices[0]
}
$Script:AdbSerial = $connectedDevice.Id
Write-Success "Connected to device: $($connectedDevice.Id)"

# Handle direct dial
if ($Dial) {
    Start-PhoneCall -Number $Dial
    Show-InCallMenu -Number $Dial
    exit 0
}

# Show status only
if ($Status) {
    $info = Get-PhoneInfo
    $state = Get-CallState
    Write-Host ""
    Write-Host "  Phone: $($info.Brand) $($info.Model)" -ForegroundColor White
    Write-Host "  Android: $($info.Android)" -ForegroundColor Gray
    Write-Host "  Battery: $($info.Battery)%" -ForegroundColor Gray
    Write-Host "  Carrier: $($info.Carrier)" -ForegroundColor Gray
    $stateClr = if ($state -eq "In Call") { "Green" } else { "Gray" }
    Write-Host "  Call State: $state" -ForegroundColor $stateClr
    Write-Host ""
    exit 0
}

# Launch interactive menu
Start-InteractiveMenu

