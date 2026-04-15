"""
Phone Call Controller (Android + ADB)
======================================
Features:
  • Outgoing call  – dials a number via ADB
  • Incoming call  – monitors phone state and auto-answers
  • Speaker output – phone audio plays through laptop speaker (via scrcpy)
  • Mic input      – laptop mic sent to phone (via scrcpy)
  • Recording      – records BOTH sides of the call:
                       • Your voice  : captured from laptop mic (InputStream)
                       • Other party : captured via WASAPI loopback from speakers
                     Both mixed and saved as a single stereo WAV file.

Requirements:
  pip install sounddevice soundfile numpy
  ADB in PATH  (Android SDK Platform-Tools)
  scrcpy installed or auto-downloaded
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

# ─── Config ───────────────────────────────────────────────────────────────────
PHONE_NUMBER    = 9873591017          # Outgoing call number (or set to None to type each time)
SAMPLE_RATE     = 44100               # Recording sample rate (Hz)
CHUNK_MS        = 100                 # Recording chunk size in ms
CALL_POLL_SEC   = 1.0                 # How often to poll phone state (incoming mode)
RECORDING_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
# ──────────────────────────────────────────────────────────────────────────────

# Set at runtime by check_devices(); used by all ADB helpers
DEVICE_ID: str = ""


# ─────────────────────────── ADB helpers ──────────────────────────────────────

def adb(*args, **kwargs):
    """
    Wrapper around subprocess that always targets DEVICE_ID with -s.
    Accepts the same kwargs as subprocess.run / Popen.
    """
    cmd = ["adb"]
    if DEVICE_ID:
        cmd += ["-s", DEVICE_ID]
    cmd += list(args)
    return cmd


def check_adb():
    """Ensure ADB is installed and accessible."""
    try:
        subprocess.run(["adb", "version"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[!] ADB not found. Install Android SDK Platform-Tools and add to PATH.")
        return False


def check_devices():
    """Return the first connected ADB device ID, or False."""
    global DEVICE_ID
    result = subprocess.run(["adb", "devices"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    lines = result.stdout.strip().split('\n')
    devices = [ln.split('\t')[0] for ln in lines[1:] if '\tdevice' in ln]
    if not devices:
        print("[!] No Android device connected via ADB.")
        print("    Enable USB Debugging on your phone and connect via USB or WiFi.")
        return False
    DEVICE_ID = devices[0]
    return DEVICE_ID


def get_call_state():
    """
    Read current phone call state via ADB.
    Returns: 'IDLE', 'RINGING', 'OFFHOOK', or 'UNKNOWN'
    """
    try:
        result = subprocess.run(
            adb("shell", "dumpsys", "telephony.registry"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            line_lower = line.lower()
            if "mcallstate" in line_lower or "call state" in line_lower:
                if "2" in line or "ringing" in line_lower:
                    return "RINGING"
                elif "1" in line or "offhook" in line_lower:
                    return "OFFHOOK"
                elif "0" in line or "idle" in line_lower:
                    return "IDLE"
    except Exception:
        pass
    return "UNKNOWN"


def answer_call():
    """Answer an incoming call via ADB (volume-up trick + HEADSETHOOK)."""
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_WAKEUP"), check=False)
    time.sleep(0.3)
    # Accept with headset hook (works on most Android versions)
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_HEADSETHOOK"), check=False)
    time.sleep(0.3)
    # Also try CALL keyevent as fallback
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_CALL"), check=False)


def make_outgoing_call(phone_number):
    """Initiate an outgoing cellular call via ADB."""
    print(f"\n[→] Dialing {phone_number}...")
    try:
        subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_WAKEUP"), check=False)
        time.sleep(0.5)
        subprocess.run(
            adb("shell", "am", "start", "-a",
                "android.intent.action.CALL", "-d", f"tel:{phone_number}"),
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        # Handle dual-SIM confirmation prompt (if any)
        time.sleep(4)
        subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_ENTER"), check=False)
        subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_CALL"),  check=False)
        print("[→] Call initiated. Waiting for connection...")
        time.sleep(8)
    except subprocess.CalledProcessError:
        print("[!] Failed to dial. Ensure PHONE permission is granted to ADB.")


def end_call():
    """Hang up the current call via ADB."""
    print("\n[✖] Ending call...")
    subprocess.run(adb("shell", "input", "keyevent", "KEYCODE_ENDCALL"), check=False)
    print("[✖] Call ended.")


# ─────────────────────────── scrcpy helpers ───────────────────────────────────

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
    Launch two scrcpy processes:
      1. phone speaker  → laptop SPEAKER  (--audio-source=output)
      2. laptop mic     → phone mic       (--audio-source=mic)
    Returns list of (label, Popen) tuples.
    """
    print("\n[~] Starting audio forwarding via scrcpy...")
    procs = []

    # Build the base scrcpy command with the device serial so scrcpy also
    # targets the correct device when multiple are connected.
    scrcpy_serial = ["-s", DEVICE_ID] if DEVICE_ID else []

    # ── Phone speaker → Laptop speaker ───────────────────────────────────────
    try:
        p = subprocess.Popen(
            ["scrcpy"] + scrcpy_serial + ["--no-video", "--audio-source=output"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        procs.append(("phone→speaker", p))
        print("[✔] Phone audio → Laptop speaker  (you hear the other person)")
    except FileNotFoundError:
        print("[!] scrcpy not found for audio output forwarding.")

    # ── Laptop mic → Phone mic ────────────────────────────────────────────────
    # Note: requires Android 14+ on the phone. Silently skipped on older Android.
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
    """Terminate all scrcpy audio-forwarding processes."""
    for label, proc in procs:
        print(f"[~] Stopping audio forwarding ({label})...")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


# ─────────────────────────── recording helpers ────────────────────────────────

def find_loopback_device():
    """
    Find a WASAPI loopback input device (captures what plays through speakers).
    Returns (device_index, device_name) or (None, None).
    """
    if not SOUNDDEVICE_AVAILABLE:
        return None, None
    try:
        devices   = sd.query_devices()
        host_apis = sd.query_hostapis()

        # Find the WASAPI host API index
        wasapi_idx = None
        for i, api in enumerate(host_apis):
            if "wasapi" in api['name'].lower():
                wasapi_idx = i
                break

        if wasapi_idx is None:
            return None, None

        # Look for loopback devices (they show as inputs but belong to output devices)
        for i, d in enumerate(devices):
            if d.get('hostapi') == wasapi_idx and d['max_input_channels'] > 0:
                name_lower = d['name'].lower()
                if 'loopback' in name_lower or 'stereo mix' in name_lower \
                        or 'what u hear' in name_lower or 'wave out' in name_lower:
                    return i, d['name']

        # Fallback: try to open the default OUTPUT device as a loopback input
        # sounddevice supports this on Windows WASAPI with hostapi_specific_stream_info
        default_out = sd.default.device[1]
        if default_out is not None and default_out >= 0:
            out_dev = devices[default_out]
            if out_dev.get('hostapi') == wasapi_idx:
                return default_out, f"{out_dev['name']} (loopback)"
    except Exception:
        pass
    return None, None


def find_mic_device():
    """Return (index, name) for the best microphone input device."""
    if not SOUNDDEVICE_AVAILABLE:
        return None, None
    try:
        devices = sd.query_devices()
        mic_keywords = ["jack mic", "microphone", "mic", "headset", "realtek"]
        for i, d in enumerate(devices):
            if d['max_input_channels'] > 0:
                if any(kw in d['name'].lower() for kw in mic_keywords):
                    return i, d['name']
        # Fallback to system default input
        default_in = sd.default.device[0]
        if default_in is not None and default_in >= 0:
            return default_in, devices[default_in]['name']
    except Exception:
        pass
    return None, None


def find_speaker_device():
    """Return (index, name) for the default speaker output device."""
    if not SOUNDDEVICE_AVAILABLE:
        return None, None
    try:
        devices = sd.query_devices()
        speaker_keywords = ["speaker", "realtek", "hd audio", "sound mapper"]
        for i, d in enumerate(devices):
            if d['max_output_channels'] > 0:
                if any(kw in d['name'].lower() for kw in speaker_keywords):
                    return i, d['name']
        default_out = sd.default.device[1]
        if default_out is not None and default_out >= 0:
            return default_out, devices[default_out]['name']
    except Exception:
        pass
    return None, None


def _record_stream(stream_device, channels, stop_event, frames_list, label):
    """Record from one audio device into frames_list until stop_event is set."""
    chunk = int(SAMPLE_RATE * CHUNK_MS / 1000)
    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=channels,
            dtype='float32',
            device=stream_device,
        ) as s:
            print(f"[●] Recording {label}...")
            while not stop_event.is_set():
                data, _ = s.read(chunk)
                frames_list.append(data.copy())
    except Exception as e:
        print(f"\n[!] Recording error ({label}): {e}")


def start_dual_recording(mic_device, loopback_device, out_path_mic,
                         out_path_speaker, stop_event):
    """
    Start two recording threads:
      • Mic stream       → records your voice
      • Loopback stream  → records phone audio (what comes out of speaker)
    Returns list of threads.
    """
    threads = []

    # ── Mic recording ─────────────────────────────────────────────────────────
    if mic_device is not None:
        mic_frames = []
        t_mic = threading.Thread(
            target=_record_stream,
            args=(mic_device, 1, stop_event, mic_frames, "your mic (outgoing)"),
            daemon=True
        )
        t_mic.start()
        threads.append(("mic", t_mic, mic_frames, out_path_mic, 1))
    else:
        print("[!] No mic device found. Your voice will not be recorded.")

    # ── Loopback / speaker recording ──────────────────────────────────────────
    if loopback_device is not None:
        spk_frames = []
        t_spk = threading.Thread(
            target=_record_stream,
            args=(loopback_device, 2, stop_event, spk_frames,
                  "speaker output (incoming)"),
            daemon=True
        )
        t_spk.start()
        threads.append(("speaker", t_spk, spk_frames, out_path_speaker, 2))
    else:
        print("[!] No loopback device found. Incoming audio will not be recorded.")
        print("    Tip: Enable 'Stereo Mix' in Windows Sound settings to record speaker output.")

    return threads


def save_recordings(threads):
    """Save all recorded audio to WAV files."""
    print("\n[~] Saving recordings...")
    for label, thread, frames, path, ch in threads:
        thread.join(timeout=5)
        if frames:
            audio = np.concatenate(frames, axis=0)
            # Ensure mono arrays are 2D
            if audio.ndim == 1:
                audio = audio.reshape(-1, 1)
            sf.write(path, audio, SAMPLE_RATE)
            dur = len(audio) / SAMPLE_RATE
            print(f"[✔] Saved ({label}): {path}  [{dur:.1f}s]")
        else:
            print(f"[!] No audio captured for: {label}")


# ─────────────────────────── call session ─────────────────────────────────────

def run_call_session(stop_event, mic_device, loopback_device, timestamp):
    """
    Common session logic after a call is active:
      1. Start scrcpy audio forwarding
      2. Start dual recording
      3. Wait for Enter to hang up
      4. Cleanup
    """
    os.makedirs(RECORDING_DIR, exist_ok=True)
    path_mic  = os.path.join(RECORDING_DIR, f"call_{timestamp}_MIC.wav")
    path_spk  = os.path.join(RECORDING_DIR, f"call_{timestamp}_SPEAKER.wav")

    # Audio forwarding (phone ↔ laptop speaker/mic via scrcpy)
    audio_procs = []
    if check_scrcpy():
        audio_procs = start_audio_forwarding()
    else:
        print("[!] scrcpy unavailable — audio not forwarded to laptop.")

    # Dual recording
    rec_stop   = threading.Event()
    rec_threads = []
    if SOUNDDEVICE_AVAILABLE:
        rec_threads = start_dual_recording(mic_device, loopback_device,
                                           path_mic, path_spk, rec_stop)
    else:
        print("[!] Recording disabled (install sounddevice/soundfile/numpy).")

    # Wait for user to hang up
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║  CALL ACTIVE — speak into your laptop mic            ║")
    print("║  Press  Enter  to hang up                            ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    try:
        input()
    except KeyboardInterrupt:
        pass

    # Stop recording first (before ending call)
    rec_stop.set()

    # End the call
    end_call()

    # Stop scrcpy
    stop_audio_forwarding(audio_procs)

    # Save recordings
    if rec_threads:
        save_recordings(rec_threads)
        print(f"\n[✔] Recordings saved to: {RECORDING_DIR}")
    else:
        print("\n[~] No recordings to save.")


# ─────────────────────────── incoming call watcher ────────────────────────────

def wait_for_incoming_call():
    """
    Block until the phone starts ringing (call state = RINGING),
    then auto-answer it. Returns True when answered.
    """
    print("\n[~] Waiting for an incoming call on your phone...")
    print("    (The phone will be auto-answered when it rings)")
    print("    Press Ctrl+C to cancel.\n")
    try:
        while True:
            state = get_call_state()
            if state == "RINGING":
                print("[♫] INCOMING CALL detected! Auto-answering...")
                time.sleep(0.5)   # Brief pause so the ring is registered
                answer_call()
                print("[✔] Call answered.")
                time.sleep(3)     # Give the call a moment to connect
                return True
            elif state == "OFFHOOK":
                print("[?] Phone already in a call. Joining session...")
                return True
            time.sleep(CALL_POLL_SEC)
    except KeyboardInterrupt:
        print("\n[~] Cancelled waiting for incoming call.")
        return False


# ─────────────────────────── main ─────────────────────────────────────────────

def main():
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

    # ── Choose mode ───────────────────────────────────────────────────────────
    print("Call mode:")
    print("  1 - Outgoing call (dial a number)")
    print("  2 - Incoming call (wait for ring and auto-answer)")
    choice = input("\nChoose [1/2]: ").strip()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # ── Detect audio devices ──────────────────────────────────────────────────
    if SOUNDDEVICE_AVAILABLE:
        print("\n[~] Detecting audio devices...")
        mic_idx,      mic_name      = find_mic_device()
        loopback_idx, loopback_name = find_loopback_device()
        spk_idx,      spk_name      = find_speaker_device()

        print(f"  Mic (your voice, recording)      : {mic_name or 'not found'}")
        print(f"  Speaker loopback (record incoming): {loopback_name or 'not found — Stereo Mix not enabled'}")
        print(f"  Speaker (call playback)           : {spk_name or 'system default'}")
    else:
        mic_idx = loopback_idx = None

    stop_event = threading.Event()

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
        run_call_session(stop_event, mic_idx, loopback_idx, timestamp)

    # ── Mode 2 : Incoming ─────────────────────────────────────────────────────
    elif choice == "2":
        answered = wait_for_incoming_call()
        if answered:
            run_call_session(stop_event, mic_idx, loopback_idx, timestamp)
        else:
            print("[~] Exiting.")

    else:
        print("[!] Invalid choice.")
        sys.exit(1)


if __name__ == "__main__":
    main()
