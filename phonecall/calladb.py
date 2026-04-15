"""
Phone Call Controller (Android + ADB)
======================================
Features:
  • Outgoing call  – dials a number via ADB
  • Incoming call  – monitors phone state and auto-answers
  • Speaker output – phone audio plays through laptop speaker (via scrcpy)
  • Mic input      – laptop mic sent to phone (via scrcpy)
  • Recording      – records BOTH sides of the call:
                       • Your voice  : captured from laptop mic
                       • Other party : captured via Stereo Mix / loopback

Requirements:
  pip install sounddevice soundfile numpy
  ADB in PATH  (Android SDK Platform-Tools)
  scrcpy installed  (winget install Genymobile.scrcpy)

Run with --list-devices to see all audio device names/indices.
"""

import subprocess
import time
import sys
import os
import threading
import datetime
import urllib.request
import zipfile
import io

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False
    print("[!] sounddevice/soundfile/numpy not installed.")
    print("    Run: pip install sounddevice soundfile numpy")

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          USER CONFIGURATION                                 ║
# ╠══════════════════════════════════════════════════════════════════════════════╣
# ║  Run  python calladb.py --list-devices  to see all device names & indices.  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ── Phone / Call settings ─────────────────────────────────────────────────────
PHONE_NUMBER = 9873591017      # Default number to dial (set None to type each time)

# ── Audio device overrides ────────────────────────────────────────────────────
# Set to None  → auto-detect (sensible default)
# Set to an int → use that device index  (from --list-devices)
# Set to a str → match by substring of device name (case-insensitive)
#
#   Example (use index):   LAPTOP_MIC_DEVICE      = 1
#   Example (use name):    LAPTOP_SPEAKER_DEVICE   = "Realtek"
#   Example (auto):        STEREO_MIX_DEVICE       = None

LAPTOP_MIC_DEVICE     = 1      # Input  – laptop mic (records YOUR voice)
                               #            1 = Microphone Array (Intel® Smart Sound)
                               #           16 = Microphone (Realtek HD Audio Mic input)
                               #  Set None for auto-detect

STEREO_MIX_DEVICE     = 2      # Input  – Stereo Mix / loopback (records OTHER person)
                               #            2 = Stereo Mix (Realtek(R) Audio)  [MME]
                               #           23 = Stereo Mix (Realtek HD Audio Stereo input)
                               #  Set None for auto-detect

# ── scrcpy audio forwarding toggles ──────────────────────────────────────────
# Phone audio → Laptop speaker: plays the call through your laptop speakers
ENABLE_PHONE_AUDIO_TO_LAPTOP = True
# Laptop mic  → Phone mic: sends your laptop mic to the phone
# (requires Android 14+; set False if the other person can't hear you)
ENABLE_LAPTOP_MIC_TO_PHONE   = False

# ── Recording / misc ─────────────────────────────────────────────────────────
CHUNK_MS       = 100      # Recording chunk size in ms
CALL_POLL_SEC  = 1.0      # How often to poll phone state (incoming mode)
RECORDING_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")

# ─────────────────────────────────────────────────────────────────────────────

# Set at runtime by check_devices()
DEVICE_ID: str = ""


# ═══════════════════════════════ ADB helpers ══════════════════════════════════

def adb(*args):
    """Build an ADB command list that always targets DEVICE_ID with -s."""
    cmd = ["adb"]
    if DEVICE_ID:
        cmd += ["-s", DEVICE_ID]
    cmd += list(args)
    return cmd


def check_adb():
    try:
        subprocess.run(["adb", "version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[!] ADB not found. Install Android SDK Platform-Tools and add to PATH.")
        return False


def check_devices(silent=False):
    """
    Detect connected ADB devices; updates global DEVICE_ID.
    If exactly one device is connected, DEVICE_ID is set to its serial.
    If multiple devices, the first is chosen.
    Returns serial string or False.
    """
    global DEVICE_ID
    result = subprocess.run(["adb", "devices"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    lines = result.stdout.strip().split('\n')
    # Accept both 'device' and 'device' (not 'offline', 'unauthorized')
    devices = [ln.split('\t')[0].strip() for ln in lines[1:]
               if '\tdevice' in ln and '\tdevice:' not in ln]
    if not devices:
        if not silent:
            print("[!] No Android device connected via ADB.")
            print("    Enable USB Debugging on your phone and connect via USB or WiFi.")
        DEVICE_ID = ""
        return False
    DEVICE_ID = devices[0]
    return DEVICE_ID


def ensure_device():
    """
    Re-verify the device is still reachable; refresh DEVICE_ID if needed.
    Returns True if a device is available.
    """
    global DEVICE_ID
    # Quick test: try to run a harmless command on the current device
    if DEVICE_ID:
        test = subprocess.run(["adb", "-s", DEVICE_ID, "get-state"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if "device" in test.stdout:
            return True
    # Re-detect
    return bool(check_devices(silent=True))


def get_call_state():
    """Returns 'IDLE', 'RINGING', 'OFFHOOK', or 'UNKNOWN'."""
    try:
        result = subprocess.run(
            adb("shell", "dumpsys", "telephony.registry"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            ll = line.lower()
            if "mcallstate" in ll or "call state" in ll:
                if "2" in line or "ringing" in ll:
                    return "RINGING"
                elif "1" in line or "offhook" in ll:
                    return "OFFHOOK"
                elif "0" in line or "idle" in ll:
                    return "IDLE"
    except Exception:
        pass
    return "UNKNOWN"


def answer_call():
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_WAKEUP"), check=False)
    time.sleep(0.3)
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_HEADSETHOOK"), check=False)
    time.sleep(0.3)
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_CALL"), check=False)


def make_outgoing_call(phone_number):
    print(f"\n[→] Dialing {phone_number}...")
    if not ensure_device():
        print("[!] Device lost. Reconnect USB and try again.")
        return
    try:
        subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_WAKEUP"), check=False)
        time.sleep(0.5)
        subprocess.run(
            adb("shell", "am", "start", "-a",
                "android.intent.action.CALL", "-d", f"tel:{phone_number}"),
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(4)
        subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_ENTER"), check=False)
        subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_CALL"),  check=False)
        print("[→] Call initiated. Waiting for connection...")
        time.sleep(8)
    except subprocess.CalledProcessError:
        print("[!] Failed to dial. Ensure PHONE permission is granted to ADB.")


def end_call():
    print("\n[✖] Ending call...")
    ensure_device()   # refresh device id in case it reconnected
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_ENDCALL"), check=False)
    print("[✖] Call ended.")


# ══════════════════════════════ scrcpy helpers ════════════════════════════════

def check_scrcpy():
    """Ensure scrcpy is available (auto-download for Windows if missing)."""
    local_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "scrcpy", "scrcpy.exe")
    if os.path.exists(local_exe):
        os.environ["PATH"] = (os.path.dirname(local_exe) +
                              os.pathsep + os.environ.get("PATH", ""))
        return True
    try:
        subprocess.run(["scrcpy", "--version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[~] scrcpy not found. Downloading...")
        try:
            url = ("https://github.com/Genymobile/scrcpy/releases/download"
                   "/v3.1/scrcpy-win64-v3.1.zip")
            response = urllib.request.urlopen(url)
            scrcpy_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrcpy")
            with zipfile.ZipFile(io.BytesIO(response.read())) as zf:
                zf.extractall(scrcpy_dir)
                folders = [f.path for f in os.scandir(scrcpy_dir) if f.is_dir()]
                os.environ["PATH"] = (folders[0] if folders else scrcpy_dir) + \
                                     os.pathsep + os.environ.get("PATH", "")
            print("[✔] scrcpy downloaded.")
            return True
        except Exception as e:
            print(f"[!] Could not download scrcpy: {e}")
            print("    Install manually: winget install Genymobile.scrcpy")
            return False


def start_audio_forwarding():
    """
    Launch scrcpy processes for audio forwarding based on config flags.
    Returns list of (label, Popen) tuples.
    """
    print("\n[~] Starting audio forwarding via scrcpy...")
    procs = []
    scrcpy_serial = ["-s", DEVICE_ID] if DEVICE_ID else []

    if ENABLE_PHONE_AUDIO_TO_LAPTOP:
        try:
            p = subprocess.Popen(
                ["scrcpy"] + scrcpy_serial + ["--no-video", "--audio-source=output"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            procs.append(("phone→speaker", p))
            print("[✔] Phone audio → Laptop speaker  (you hear the other person)")
        except FileNotFoundError:
            print("[!] scrcpy not found. Install: winget install Genymobile.scrcpy")

    if ENABLE_LAPTOP_MIC_TO_PHONE:
        try:
            p = subprocess.Popen(
                ["scrcpy"] + scrcpy_serial + ["--no-video", "--audio-source=mic"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            procs.append(("laptop mic→phone", p))
            print("[✔] Laptop mic → Phone mic         (other person hears you)")
        except FileNotFoundError:
            print("[!] scrcpy not found for mic forwarding.")

    return procs


def stop_audio_forwarding(procs):
    for label, proc in procs:
        print(f"[~] Stopping audio forwarding ({label})...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


# ══════════════════════════════ audio device helpers ══════════════════════════

def list_devices():
    """Print all available audio devices — useful for configuring device overrides."""
    if not SOUNDDEVICE_AVAILABLE:
        print("[!] sounddevice not installed.")
        return
    devices = sd.query_devices()
    print("\n─── Available Audio Devices ───────────────────────────────────────────")
    print(f"  {'IDX':>3}  {'IN':>3}  {'OUT':>3}  NAME")
    print(f"  {'───':>3}  {'──':>3}  {'───':>3}  ────────────────────────────────────")
    for i, d in enumerate(devices):
        marker = " ◄" if i == sd.default.device[0] or i == sd.default.device[1] else ""
        print(f"  {i:>3}  {d['max_input_channels']:>3}  {d['max_output_channels']:>3}  "
              f"{d['name']}{marker}")
    print("\n  IN = input channels, OUT = output channels")
    print("  ◄ = system default")
    print("\n  Use these indices or name substrings in the CONFIG section of calladb.py")
    print("─" * 72)


def _resolve_device(config_val, want_input: bool):
    """
    Resolve a config value (None / int / str) to a (device_index, device_name) tuple.
    Returns (None, None) if not found.
    """
    if not SOUNDDEVICE_AVAILABLE:
        return None, None

    devices = sd.query_devices()
    channel_key = 'max_input_channels' if want_input else 'max_output_channels'

    # Integer → use directly
    if isinstance(config_val, int):
        if 0 <= config_val < len(devices):
            d = devices[config_val]
            if d[channel_key] > 0:
                return config_val, d['name']
            print(f"[!] Device #{config_val} ({d['name']}) has no "
                  f"{'input' if want_input else 'output'} channels.")
        else:
            print(f"[!] Device index {config_val} out of range.")
        return None, None

    # String → substring match
    if isinstance(config_val, str):
        needle = config_val.lower()
        for i, d in enumerate(devices):
            if needle in d['name'].lower() and d[channel_key] > 0:
                return i, d['name']
        print(f"[!] No {'input' if want_input else 'output'} device matching '{config_val}' found.")
        return None, None

    # None → auto-detect
    if want_input:
        # Prefer Stereo Mix / loopback names for loopback context
        return None, None   # caller handles auto logic
    else:
        default_out = sd.default.device[1]
        if default_out is not None and default_out >= 0:
            return default_out, devices[default_out]['name']
        return None, None


def _auto_find_mic():
    """Auto-detect the best laptop microphone input device."""
    if not SOUNDDEVICE_AVAILABLE:
        return None, None
    try:
        devices = sd.query_devices()
        keywords = ["jack mic", "microphone", "mic", "headset", "realtek", "input"]
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                if any(kw in d['name'].lower() for kw in keywords):
                    return i, d['name']
        default_in = sd.default.device[0]
        if default_in is not None and default_in >= 0:
            return default_in, devices[default_in]['name']
    except Exception:
        pass
    return None, None


def _auto_find_loopback():
    """Auto-detect Stereo Mix / WASAPI loopback device."""
    if not SOUNDDEVICE_AVAILABLE:
        return None, None
    try:
        devices   = sd.query_devices()
        host_apis = sd.query_hostapis()

        wasapi_idx = None
        for i, api in enumerate(host_apis):
            if "wasapi" in api['name'].lower():
                wasapi_idx = i
                break

        for i, d in enumerate(devices):
            if d.get('hostapi') == wasapi_idx and d['max_input_channels'] > 0:
                nl = d['name'].lower()
                if 'stereo mix' in nl or 'loopback' in nl \
                        or 'what u hear' in nl or 'wave out' in nl:
                    return i, d['name']
    except Exception:
        pass
    return None, None


def _device_native_rate(device_index):
    """
    Probe the device for its best supported sample rate.
    MME drivers always report 44100 via default_samplerate even when the
    hardware runs at 48000 Hz, so we try common rates and pick the first
    that actually opens without error.
    """
    preferred = [48000, 44100, 16000, 22050, 8000]
    try:
        info = sd.query_devices(device_index)
        reported = int(info.get('default_samplerate', 48000))
        if reported > 0 and reported not in preferred:
            preferred.insert(0, reported)
    except Exception:
        pass

    for rate in preferred:
        try:
            with sd.InputStream(device=device_index, samplerate=rate,
                                channels=1, dtype='float32'):
                pass   # opened successfully
            return rate
        except Exception:
            continue
    return 48000  # last resort


def resolve_audio_devices():
    """
    Resolve all four audio device config values.
    Returns dict with keys: mic_idx, mic_name, loopback_idx, loopback_name
    """
    # Mic
    if LAPTOP_MIC_DEVICE is None:
        mic_idx, mic_name = _auto_find_mic()
    else:
        mic_idx, mic_name = _resolve_device(LAPTOP_MIC_DEVICE, want_input=True)

    # Stereo Mix / loopback
    if STEREO_MIX_DEVICE is None:
        loopback_idx, loopback_name = _auto_find_loopback()
    else:
        loopback_idx, loopback_name = _resolve_device(STEREO_MIX_DEVICE, want_input=True)

    return {
        "mic_idx":       mic_idx,
        "mic_name":      mic_name,
        "loopback_idx":  loopback_idx,
        "loopback_name": loopback_name,
    }


# ══════════════════════════════ recording helpers ═════════════════════════════

def _record_stream(device_idx, channels, rate, stop_event, frames_list, label):
    """Record audio from device_idx into frames_list until stop_event is set."""
    chunk = int(rate * CHUNK_MS / 1000)
    try:
        with sd.InputStream(
            samplerate=rate,
            channels=channels,
            dtype='float32',
            device=device_idx,
        ) as s:
            print(f"[●] Recording {label}  (device #{device_idx}, {rate} Hz)...")
            while not stop_event.is_set():
                data, _ = s.read(chunk)
                frames_list.append(data.copy())
    except Exception as e:
        print(f"\n[!] Recording error ({label}): {e}")


def start_dual_recording(devices, out_path_mic, out_path_speaker, stop_event):
    """
    Start recording threads for mic and loopback.
    Each device records at its own native sample rate to avoid PaErrorCode -9997.
    Returns list of (label, thread, frames, path, channels, rate) tuples.
    """
    threads = []

    mic_idx      = devices["mic_idx"]
    loopback_idx = devices["loopback_idx"]

    # ── Mic ───────────────────────────────────────────────────────────────────
    if mic_idx is not None:
        rate = _device_native_rate(mic_idx)
        frames = []
        t = threading.Thread(
            target=_record_stream,
            args=(mic_idx, 1, rate, stop_event, frames, "your mic (outgoing)"),
            daemon=True
        )
        t.start()
        threads.append(("mic", t, frames, out_path_mic, 1, rate))
    else:
        print("[!] No mic device found. Your voice will not be recorded.")
        print("    Set LAPTOP_MIC_DEVICE in the CONFIG section, or run --list-devices.")

    # ── Loopback / Stereo Mix ─────────────────────────────────────────────────
    if loopback_idx is not None:
        rate = _device_native_rate(loopback_idx)
        frames = []
        t = threading.Thread(
            target=_record_stream,
            args=(loopback_idx, 2, rate, stop_event, frames, "speaker/Stereo Mix (incoming)"),
            daemon=True
        )
        t.start()
        threads.append(("speaker", t, frames, out_path_speaker, 2, rate))
    else:
        print("[!] No loopback device found. Incoming audio will not be recorded.")
        print("    Tip: Enable 'Stereo Mix' in Windows Sound settings,")
        print("         then set STEREO_MIX_DEVICE = 'Stereo Mix' in the CONFIG.")

    return threads


def save_recordings(threads):
    """Save all recorded audio to WAV files."""
    print("\n[~] Saving recordings...")
    for label, thread, frames, path, ch, rate in threads:
        thread.join(timeout=5)
        if frames:
            audio = np.concatenate(frames, axis=0)
            if audio.ndim == 1:
                audio = audio.reshape(-1, 1)
            sf.write(path, audio, rate)
            dur = len(audio) / rate
            print(f"[✔] Saved ({label}): {path}  [{dur:.1f}s]")
        else:
            print(f"[!] No audio captured for: {label}")


# ══════════════════════════════ call session ══════════════════════════════════

def run_call_session(audio_devices, timestamp):
    os.makedirs(RECORDING_DIR, exist_ok=True)
    path_mic = os.path.join(RECORDING_DIR, f"call_{timestamp}_MIC.wav")
    path_spk = os.path.join(RECORDING_DIR, f"call_{timestamp}_SPEAKER.wav")

    # Audio forwarding (phone ↔ laptop via scrcpy)
    audio_procs = []
    if check_scrcpy():
        audio_procs = start_audio_forwarding()
    else:
        print("[!] scrcpy unavailable — audio not forwarded to laptop.")

    # Recording
    rec_stop    = threading.Event()
    rec_threads = []
    if SOUNDDEVICE_AVAILABLE:
        rec_threads = start_dual_recording(audio_devices, path_mic, path_spk, rec_stop)
    else:
        print("[!] Recording disabled (install sounddevice/soundfile/numpy).")

    print("\n╔══════════════════════════════════════════════════════╗")
    print("║  CALL ACTIVE — speak into your laptop mic            ║")
    print("║  Press  Enter  to hang up                            ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    try:
        input()
    except KeyboardInterrupt:
        pass

    rec_stop.set()
    end_call()
    stop_audio_forwarding(audio_procs)

    if rec_threads:
        save_recordings(rec_threads)
        print(f"\n[✔] Recordings saved to: {RECORDING_DIR}")
    else:
        print("\n[~] No recordings to save.")


# ══════════════════════════════ incoming call watcher ════════════════════════

def wait_for_incoming_call():
    print("\n[~] Waiting for an incoming call on your phone...")
    print("    (The phone will be auto-answered when it rings)")
    print("    Press Ctrl+C to cancel.\n")
    try:
        while True:
            state = get_call_state()
            if state == "RINGING":
                print("[♫] INCOMING CALL detected! Auto-answering...")
                time.sleep(0.5)
                answer_call()
                print("[✔] Call answered.")
                time.sleep(3)
                return True
            elif state == "OFFHOOK":
                print("[?] Phone already in a call. Joining session...")
                return True
            time.sleep(CALL_POLL_SEC)
    except KeyboardInterrupt:
        print("\n[~] Cancelled waiting for incoming call.")
        return False


# ══════════════════════════════ main ═════════════════════════════════════════

def main():
    # ── --list-devices flag ───────────────────────────────────────────────────
    if "--list-devices" in sys.argv:
        list_devices()
        return

    print("╔══════════════════════════════════════════════════════════╗")
    print("║     Phone Call Controller  (Android + ADB)              ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  Records both sides:                                     ║")
    print("║    MIC (your voice)  +  SPEAKER (other person's voice)  ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    if not check_adb():
        sys.exit(1)

    device_id = check_devices()
    if not device_id:
        sys.exit(1)
    print(f"[✔] Connected to: {device_id}\n")

    # ── Choose call mode ──────────────────────────────────────────────────────
    print("Call mode:")
    print("  1 - Outgoing call (dial a number)")
    print("  2 - Incoming call (wait for ring and auto-answer)")
    choice = input("\nChoose [1/2]: ").strip()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Resolve audio devices ─────────────────────────────────────────────────
    audio_devices = {"mic_idx": None, "mic_name": None,
                     "loopback_idx": None, "loopback_name": None}
    if SOUNDDEVICE_AVAILABLE:
        print("\n[~] Resolving audio devices...")
        audio_devices = resolve_audio_devices()
        mic_label      = audio_devices["mic_name"]      or "not found"
        loopback_label = audio_devices["loopback_name"] or "not found — Stereo Mix not enabled"
        print(f"  Laptop Mic    (record YOUR voice) : {mic_label}")
        print(f"  Stereo Mix    (record THEIR voice): {loopback_label}")
        print(f"  Phone audio → Laptop speaker      : {'enabled' if ENABLE_PHONE_AUDIO_TO_LAPTOP else 'disabled'}")
        print(f"  Laptop mic  → Phone mic           : {'enabled' if ENABLE_LAPTOP_MIC_TO_PHONE else 'disabled'}")
        print("\n  Tip: Run  python calladb.py --list-devices  to see all device options.")

    # ── Mode 1 : Outgoing ─────────────────────────────────────────────────────
    if choice == "1":
        if PHONE_NUMBER:
            number = str(PHONE_NUMBER)
        else:
            number = input("Enter phone number to dial (e.g. +919876543210): ").strip()
        if not number:
            print("[!] Invalid number.")
            sys.exit(1)
        make_outgoing_call(number)
        run_call_session(audio_devices, timestamp)

    # ── Mode 2 : Incoming ─────────────────────────────────────────────────────
    elif choice == "2":
        answered = wait_for_incoming_call()
        if answered:
            run_call_session(audio_devices, timestamp)
        else:
            print("[~] Exiting.")

    else:
        print("[!] Invalid choice.")
        sys.exit(1)


if __name__ == "__main__":
    main()
