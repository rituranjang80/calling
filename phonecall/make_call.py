import sys
import pjsua as pj
import time

# --- Configuration ---
# Replace these with your SIP server details (e.g., your Asterisk server)
SIP_DOMAIN = "192.168.1.100"  # Your Asterisk IP or SIP provider domain
SIP_USER = "100"              # Your SIP extension/username
SIP_PASSWORD = "password"     # Your SIP password

# The number or extension you want to call
DESTINATION_URI = "sip:101@" + SIP_DOMAIN 
# ---------------------

def log_cb(level, str, len):
    print(str, end='')

class MyAccountCallback(pj.AccountCallback):
    def __init__(self, account):
        pj.AccountCallback.__init__(self, account)

class MyCallCallback(pj.CallCallback):
    def __init__(self, call=None):
        pj.CallCallback.__init__(self, call)

    def on_state(self):
        print("Call state:", self.call.info().state_text)
        if self.call.info().state == pj.CallState.DISCONNECTED:
            print("Call disconnected. Reason:", self.call.info().last_reason)

    def on_media_state(self):
        if self.call.info().media_state == pj.MediaState.ACTIVE:
            print("Media is active! Connecting audio to laptop speaker/mic...")
            # Connect the call to the sound device (laptop speakers and microphone)
            call_slot = self.call.info().conf_slot
            pj.Lib.instance().conf_connect(call_slot, 0)
            pj.Lib.instance().conf_connect(0, call_slot)
            print("Audio connected. You can talk now!")

try:
    # 1. Initialize the library
    lib = pj.Lib()
    lib.init(log_cfg=pj.LogConfig(level=3, callback=log_cb))

    # 2. Create UDP transport
    transport = lib.create_transport(pj.TransportType.UDP)

    # 3. Start the library
    lib.start()

    # 4. Configure local audio devices
    # 0 is usually the default system sound device (laptop mic/speaker)
    lib.set_snd_dev(0, 0) 

    # 5. Create and configure the SIP account
    acc_cfg = pj.AccountConfig(SIP_DOMAIN, SIP_USER, SIP_PASSWORD)
    acc = lib.create_account(acc_cfg)
    acc_cb = MyAccountCallback(acc)
    acc.set_callback(acc_cb)
    print(f"Registered SIP account: {SIP_USER}@{SIP_DOMAIN}")

    # 6. Make the call
    print(f"Calling {DESTINATION_URI}...")
    call = acc.make_call(DESTINATION_URI, MyCallCallback())

    # Wait for the user to end the call
    print("Press Enter to hang up...")
    sys.stdin.readline()

    # Hang up
    call.hangup()

except pj.Error as e:
    print("Exception: " + str(e))
except KeyboardInterrupt:
    print("\nCall interrupted by user.")
finally:
    # Destroy the library to cleanly shut down
    lib.destroy()
    lib = None
