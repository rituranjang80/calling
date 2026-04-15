# Phone Call Controller

Make and receive GSM/cellular phone calls from your Windows laptop through your Android phone using ADB.

## What This Does

Your phone acts as the cellular modem — this tool sends commands via ADB to:
- **Dial phone numbers** from your laptop
- **Answer incoming calls**
- **Hang up** active calls
- **Send SMS** messages
- **Send DTMF tones** (for IVR menus like "press 1 for...")
- **Manage contacts** and call log
- Works over **USB** or **WiFi** (wireless)

> **Audio stays on your phone** — use speakerphone or a Bluetooth headset connected to your phone for hands-free calling.

## Quick Start

### 1. One-Time Setup

```powershell
cd c:\RandR\phone-call
.\setup.ps1
```

This will:
- Install ADB if not already present
- Guide you through enabling USB Debugging on your phone
- Verify the connection
- Optionally set up wireless (WiFi) ADB

### 2. Enable USB Debugging (on your Android phone)

1. Go to **Settings > About Phone**
2. Tap **Build Number** 7 times (enables Developer Options)
3. Go to **Settings > Developer Options**
4. Turn on **USB Debugging**
5. Connect phone to laptop via USB cable
6. Accept the **"Allow USB Debugging?"** popup on your phone

### 3. Start Making Calls

```powershell
# Interactive menu (recommended)
.\call.ps1

# Quick dial a number
.\call.ps1 -Dial "+1234567890"

# Check phone status
.\call.ps1 -Status

# Connect wirelessly (after initial USB setup)
.\call.ps1 -Phone "192.168.1.100"
```

## Features

| Feature | Command |
|---------|---------|
| Interactive menu | `.\call.ps1` |
| Quick dial | `.\call.ps1 -Dial "+1234567890"` |
| Phone status | `.\call.ps1 -Status` |
| WiFi connect | `.\call.ps1 -Phone "192.168.1.100"` |
| Disconnect WiFi | `.\call.ps1 -Disconnect` |

### In the Interactive Menu

| Option | Action |
|--------|--------|
| 1 | Dial a phone number |
| 2 | Call from saved contacts |
| 3 | Answer incoming call |
| 4 | Hang up / end call |
| 5 | Send SMS |
| 6 | Manage contacts (add/list/phone contacts) |
| 7 | View call log |
| 8 | Open phone dialer |
| 9 | WiFi ADB connect/disconnect |
| 0 | Exit |

### During a Call

| Key | Action |
|-----|--------|
| H | Hang up |
| M | Mute/Unmute |
| S | Volume up |
| D | Send DTMF tones (for IVR menus) |
| B | Back to main menu (call stays active) |

## WiFi (Wireless) Mode

After the initial USB setup, you can go wireless:

1. Connect phone via USB first
2. Run `.\setup.ps1` and choose WiFi setup, OR:
   ```powershell
   # The script auto-sets TCP/IP mode
   .\call.ps1 -Phone "YOUR_PHONE_IP"
   ```
3. Find your phone's IP: **Settings > WiFi > tap your network > IP address**
4. Unplug USB — you're now wireless!

## Files

| File | Purpose |
|------|---------|
| `call.ps1` | Main phone call controller |
| `setup.ps1` | One-time setup wizard |
| `contacts.json` | Saved contacts (created on first use) |
| `call_log.csv` | Call/SMS history (created on first use) |

## Troubleshooting

### "No Android device connected"
- Ensure USB cable supports **data transfer** (not charge-only)
- Check phone for "Allow USB debugging?" popup
- Try: `adb kill-server; adb start-server; adb devices`

### "Device unauthorized"
- Look at your phone screen for the authorization popup
- Check "Always allow from this computer" and tap Allow

### WiFi connection drops
- Phone and laptop must be on the **same WiFi network**
- Re-run: `.\call.ps1 -Phone "YOUR_PHONE_IP"`
- If that fails, reconnect USB and re-run setup

### Call doesn't go through
- Ensure your phone has an active SIM card with service
- Some phones require granting phone permission to shell
- Try making a call manually on the phone first to verify service

## Requirements

- **Windows 10/11** with PowerShell 5.1+
- **Android phone** with USB Debugging enabled
- **USB cable** (data-capable) for initial setup
- **Same WiFi network** for wireless mode (optional)
