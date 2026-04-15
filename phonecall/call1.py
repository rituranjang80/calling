import subprocess
import time
import sys
import os
import urllib.request
import zipfile
import io
from datetime import datetime  # <-- NEW: for timestamp

def check_adb():
    # ... (unchanged)
    pass

def check_scrcpy():
    # ... (unchanged)
    pass

def check_devices():
    # ... (unchanged)
    pass

def make_call(phone_number):
    # ... (unchanged)
    pass

def start_audio_forwarding(record_filename=None):  # <-- MODIFIED: accepts filename
    """Start scrcpy to forward phone audio to laptop speakers, optionally recording."""
    print("Starting audio forwarding from phone to laptop...")
    print("WARNING: You will hear the call on your laptop, but you still need to speak into the phone's microphone (or phone's bluetooth).")

    cmd = ["scrcpy", "--no-video", "--audio-source=output"]
    if record_filename:
        cmd.append(f"--record={record_filename}")
        print(f"Recording call to: {record_filename}")

    try:
        scrcpy_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return scrcpy_process
    except FileNotFoundError:
        print("Warning: Could not start audio forwarding. 'scrcpy' not found.")
        return None

def end_call():
    # ... (unchanged)
    pass

def main():
    print("=== Python Phone Call Controller (via Android & ADB) ===")

    if not check_adb():
        sys.exit(1)

    device_id = check_devices()
    if not device_id:
        sys.exit(1)

    print(f"Connected to device: {device_id}")

    number = "9873591017"  # input(...)  # <-- keep your number or use input

    # 1. Create recordings directory if it doesn't exist  <-- NEW
    recordings_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_recordings")
    os.makedirs(recordings_dir, exist_ok=True)

    # 2. Generate unique filename with timestamp and phone number  <-- NEW
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_number = "".join(c for c in number if c.isdigit())  # remove any non-digit characters
    record_filename = os.path.join(recordings_dir, f"call_{timestamp}_{safe_number}.m4a")

    # 3. Start the call
    make_call(number)

    # 4. Forward audio using scrcpy (with recording)  <-- MODIFIED
    audio_process = None
    if check_scrcpy():
        audio_process = start_audio_forwarding(record_filename)
    else:
        print("Tip: Install 'scrcpy' to forward phone speaker audio directly to your laptop headphones/speakers.")

    # 5. Wait for user to hang up
    print("\nCall is currently active. Recording in progress...")
    try:
        input("Press Enter to hang up the call...")
    except KeyboardInterrupt:
        pass

    # 6. Cleanup
    end_call()

    if audio_process:
        print("Stopping audio forwarding and saving recording...")
        audio_process.terminate()
        try:
            audio_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            audio_process.kill()

if __name__ == "__main__":
    main()