"""
FreePBX SIP Phone — call.py
============================
Make and receive calls through FreePBX (Asterisk) running in Docker.
The laptop acts as a SIP softphone (pure Python, no extra SIP libraries).

Features:
  • Outgoing call  — dial any number via FreePBX SIP trunk
  • Incoming call  — register and auto-answer when FreePBX rings
  • Speaker output — phone audio plays through laptop speaker
  • Mic input      — laptop mic sent to remote party via RTP
  • Recording      — saves mic + loopback WAV files per call
  • Call log       — appends to call_log.csv

Requirements:
  pip install sounddevice soundfile numpy

FreePBX setup (one-time):
  1. http://localhost/admin/config.php
  2. Applications → Extensions → Add Extension → chan_pjsip
     Extension: 1001  |  Secret: secret1001
  3. Apply Config
"""

import socket
import struct
import threading
import hashlib
import random
import string
import time
import datetime
import os
import csv
import sys
import subprocess
import re

try:
    import sounddevice as sd
    import soundfile as sf
    import numpy as np
    AUDIO_OK = True
except ImportError:
    AUDIO_OK = False
    print("[!] Run: pip install sounddevice soundfile numpy")

# ═══════════════════════════════════════════════════════════════════
#  CONFIG  — edit these to match your FreePBX / extension settings
# ═══════════════════════════════════════════════════════════════════
FREEPBX_IP        = "localhost"       # Docker Desktop on Windows: use localhost, not bridge IP
FREEPBX_SIP_PORT  = 5060             # PJSIP port
DOCKER_CONTAINER  = "confident_lederberg"

SIP_EXTENSION     = "1001"           # Extension created in FreePBX
SIP_PASSWORD      = "secret1001"     # Extension secret
SIP_LOCAL_PORT    = 5090             # UDP port this script listens on
RTP_LOCAL_PORT    = 10000            # UDP port for RTP audio

OUTGOING_CONTEXT  = "from-internal"  # FreePBX dialplan context

SAMPLE_RATE       = 8000             # SIP standard (G.711 = 8kHz)
CHUNK_MS          = 20               # RTP packet interval (ms)
CHUNK_FRAMES      = SAMPLE_RATE * CHUNK_MS // 1000  # 160 samples / packet

RECORDING_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
CALL_LOG_CSV      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_log.csv")

PHONE_NUMBER      = None             # Set a default number or None to prompt each time
# ═══════════════════════════════════════════════════════════════════


# ──────────────────────────────────────────────────────────────────
#  G.711 μ-law codec  (pure Python, no audioop dependency)
# ──────────────────────────────────────────────────────────────────

def lin2ulaw(sample: int) -> int:
    """Encode a 16-bit signed PCM sample to 8-bit G.711 μ-law."""
    BIAS = 0x84
    CLIP = 32635
    sign = 0
    if sample < 0:
        sample = -sample
        sign = 0x80
    if sample > CLIP:
        sample = CLIP
    sample += BIAS
    exp = 7
    for exp_mask in (0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100):
        if sample & exp_mask:
            break
        exp -= 1
    mantissa = (sample >> (exp + 3)) & 0x0F
    return (~(sign | (exp << 4) | mantissa)) & 0xFF


def ulaw2lin(u: int) -> int:
    """Decode an 8-bit G.711 μ-law byte to a 16-bit signed PCM sample."""
    u = ~u & 0xFF
    sign = u & 0x80
    exp  = (u >> 4) & 0x07
    mant = u & 0x0F
    sample = ((mant << 3) + 0x84) << exp
    return -(sample - 0x84) if sign else (sample - 0x84)


def pcm_bytes_to_ulaw(pcm: bytes) -> bytes:
    """Convert raw 16-bit LE PCM bytes → G.711 μ-law bytes."""
    num_samples = len(pcm) // 2
    samples = struct.unpack(f'<{num_samples}h', pcm)
    return bytes(lin2ulaw(s) for s in samples)


def ulaw_bytes_to_pcm(ulaw: bytes) -> bytes:
    """Convert G.711 μ-law bytes → raw 16-bit LE PCM bytes."""
    samples = [ulaw2lin(b) for b in ulaw]
    return struct.pack(f'<{len(samples)}h', *samples)


def float32_to_pcm16(arr: 'np.ndarray') -> bytes:
    """float32 numpy array [-1,1] → 16-bit LE PCM bytes."""
    clipped = np.clip(arr, -1.0, 1.0)
    return (clipped * 32767).astype('<i2').tobytes()


def pcm16_to_float32(pcm: bytes) -> 'np.ndarray':
    """16-bit LE PCM bytes → float32 numpy array."""
    return np.frombuffer(pcm, dtype='<i2').astype('float32') / 32768.0


# ──────────────────────────────────────────────────────────────────
#  RTP packet helpers
# ──────────────────────────────────────────────────────────────────

PAYLOAD_PCMU = 0   # G.711 μ-law
RTP_HEADER_SIZE = 12


def make_rtp(payload: bytes, seq: int, timestamp: int, ssrc: int,
             payload_type: int = PAYLOAD_PCMU) -> bytes:
    return struct.pack('!BBHII', 0x80, payload_type, seq, timestamp, ssrc) + payload


def parse_rtp(data: bytes):
    """Returns (payload_type, seq, timestamp, ssrc, payload) or None."""
    if len(data) < RTP_HEADER_SIZE:
        return None
    b0, b1, seq, ts, ssrc = struct.unpack('!BBHII', data[:RTP_HEADER_SIZE])
    pt = b1 & 0x7F
    return pt, seq, ts, ssrc, data[RTP_HEADER_SIZE:]


# ──────────────────────────────────────────────────────────────────
#  SIP helpers
# ──────────────────────────────────────────────────────────────────

def rand_tag(n=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=n))


def rand_call_id():
    return ''.join(random.choices(string.hexdigits.lower(), k=20))


def md5_digest_auth(username, realm, password, method, uri, nonce, nc='00000001',
                    cnonce=None, qop=None):
    """Compute SIP Digest Authorization header value."""
    if cnonce is None:
        cnonce = rand_tag(16)
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    if qop:
        response = hashlib.md5(
            f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()
        ).hexdigest()
        return (f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
                f'uri="{uri}", qop={qop}, nc={nc}, cnonce="{cnonce}", '
                f'response="{response}", algorithm=MD5')
    else:
        response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
                f'uri="{uri}", response="{response}", algorithm=MD5')


def parse_auth_header(header_val: str) -> dict:
    """Parse WWW-Authenticate or Proxy-Authenticate header into dict."""
    params = {}
    for m in re.finditer(r'(\w+)=["\']?([^"\'>,]+)["\']?', header_val):
        params[m.group(1)] = m.group(2).strip()
    return params


def get_header(msg: str, name: str) -> str:
    """Extract a SIP header value (case-insensitive)."""
    for line in msg.splitlines():
        if line.lower().startswith(name.lower() + ':'):
            return line.split(':', 1)[1].strip()
    return ''


def get_status_code(msg: str) -> int:
    try:
        return int(msg.split(' ', 2)[1])
    except Exception:
        return 0


def local_ip() -> str:
    """
    Get the local IP visible to FreePBX.
    When FreePBX is on localhost (Docker Desktop/Windows), 127.0.0.1 is correct
    for both SIP Contact headers and RTP — everything stays on loopback.
    """
    server = FREEPBX_IP
    if server in ('localhost', '127.0.0.1'):
        return '127.0.0.1'
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((server, 5060))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


# ──────────────────────────────────────────────────────────────────
#  AMI Bridge (via docker exec)
# ──────────────────────────────────────────────────────────────────

class AMIBridge:
    """Run Asterisk CLI commands via 'docker exec' for call control."""

    def _exec(self, cmd: str) -> str:
        try:
            result = subprocess.run(
                ['docker', 'exec', DOCKER_CONTAINER, 'asterisk', '-rx', cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=10
            )
            return result.stdout.strip()
        except Exception as e:
            return f"ERROR: {e}"

    def get_endpoints(self) -> str:
        return self._exec("pjsip show endpoints")

    def get_channels(self) -> str:
        return self._exec("core show channels")

    def originate(self, number: str, caller_id: str = None) -> bool:
        """
        Originate a call from extension to an outside number.
        Uses the PJSIP endpoint as originating channel.
        """
        cid = caller_id or SIP_EXTENSION
        cmd = (
            f'channel originate PJSIP/{SIP_EXTENSION} '
            f'extension {number}@{OUTGOING_CONTEXT}'
        )
        out = self._exec(cmd)
        print(f"[AMI] Originate: {out or 'OK'}")
        return True

    def hangup_extension(self) -> bool:
        """Hang up any active call on our extension."""
        channels_out = self._exec("core show channels")
        for line in channels_out.splitlines():
            if SIP_EXTENSION in line:
                parts = line.split()
                if parts:
                    chan = parts[0]
                    self._exec(f"channel request hangup {chan}")
                    return True
        return False


# ──────────────────────────────────────────────────────────────────
#  SIP Client  (raw UDP, PJSIP-compatible)
# ──────────────────────────────────────────────────────────────────

class SIPClient:
    def __init__(self):
        self.local_ip    = local_ip()
        self.local_port  = SIP_LOCAL_PORT
        self.server      = FREEPBX_IP
        self.server_port = FREEPBX_SIP_PORT
        self.exten       = SIP_EXTENSION
        self.password    = SIP_PASSWORD
        self.call_id     = rand_call_id()
        self.tag         = rand_tag()
        self.cseq        = 1
        self.registered  = False
        self.reg_expiry  = 60          # seconds

        # Active call state
        self.peer_ip     = None
        self.peer_rtp    = None
        self.remote_tag  = None
        self.in_dialog   = False
        self.invite_msg  = None        # stored for ACK after incoming INVITE
        self.sdp_offer   = None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', self.local_port))
        self.sock.settimeout(0.5)

        self._stop  = threading.Event()
        self._queue = []               # incoming SIP messages
        self._lock  = threading.Lock()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

    # ── low-level send ────────────────────────────────────────────

    def _send(self, msg: str):
        self.sock.sendto(msg.encode(), (self.server, self.server_port))

    def _send_to(self, msg: str, addr):
        self.sock.sendto(msg.encode(), addr)

    def _recv_loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(4096)
                msg = data.decode(errors='replace')
                with self._lock:
                    self._queue.append((msg, addr))
            except socket.timeout:
                pass
            except Exception:
                pass

    def _wait_response(self, timeout=5.0):
        """Wait for next SIP message from queue."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._queue:
                    return self._queue.pop(0)
            time.sleep(0.05)
        return None, None

    def _drain_queue(self):
        with self._lock:
            self._queue.clear()

    # ── SDP builder ───────────────────────────────────────────────

    def _make_sdp(self) -> str:
        return (
            f"v=0\r\n"
            f"o={self.exten} {int(time.time())} {int(time.time())} IN IP4 {self.local_ip}\r\n"
            f"s=call\r\n"
            f"c=IN IP4 {self.local_ip}\r\n"
            f"t=0 0\r\n"
            f"m=audio {RTP_LOCAL_PORT} RTP/AVP 0\r\n"
            f"a=rtpmap:0 PCMU/8000\r\n"
            f"a=ptime:{CHUNK_MS}\r\n"
        )

    def _parse_sdp_conn(self, msg: str):
        """Extract remote RTP IP and port from SDP in a SIP message."""
        rtp_ip   = None
        rtp_port = None
        in_sdp   = False
        for line in msg.splitlines():
            if line.startswith('c=IN IP4'):
                rtp_ip = line.split()[-1]
            if line.startswith('m=audio'):
                try:
                    rtp_port = int(line.split()[1])
                except Exception:
                    pass
        return rtp_ip, rtp_port

    # ── REGISTER ──────────────────────────────────────────────────

    def register(self) -> bool:
        """Register with FreePBX. Returns True on success."""
        uri    = f"sip:{self.server}"
        from_  = f"<sip:{self.exten}@{self.server}>;tag={self.tag}"
        to_    = f"<sip:{self.exten}@{self.server}>"
        contact= f"<sip:{self.exten}@{self.local_ip}:{self.local_port}>"

        def _build(auth_header=''):
            return (
                f"REGISTER {uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch=z9hG4bK{rand_tag()}\r\n"
                f"From: {from_}\r\n"
                f"To: {to_}\r\n"
                f"Call-ID: {self.call_id}-reg\r\n"
                f"CSeq: {self.cseq} REGISTER\r\n"
                f"Contact: {contact}\r\n"
                f"Expires: {self.reg_expiry}\r\n"
                f"Max-Forwards: 70\r\n"
                f"User-Agent: PythonSIPClient/1.0\r\n"
                + (f"Authorization: {auth_header}\r\n" if auth_header else '')
                + f"Content-Length: 0\r\n\r\n"
            )

        self._drain_queue()
        self._send(_build())
        resp, _ = self._wait_response(timeout=5)
        if resp is None:
            print("[SIP] No response to REGISTER — check FreePBX IP/port.")
            return False

        code = get_status_code(resp)
        if code == 200:
            self.registered = True
            return True

        if code == 401 or code == 407:
            # Digest challenge
            wwa = get_header(resp, 'WWW-Authenticate') or get_header(resp, 'Proxy-Authenticate')
            if not wwa:
                print(f"[SIP] Auth required but no challenge header. Response: {code}")
                return False

            params = parse_auth_header(wwa)
            realm  = params.get('realm', self.server)
            nonce  = params.get('nonce', '')
            qop    = params.get('qop', '')
            req_uri= f"sip:{self.server}"

            auth = md5_digest_auth(
                self.exten, realm, self.password,
                'REGISTER', req_uri, nonce,
                qop=qop if 'auth' in qop else None
            )
            self.cseq += 1
            self._drain_queue()
            self._send(_build(auth_header=auth))
            resp2, _ = self._wait_response(timeout=5)
            if resp2 and get_status_code(resp2) == 200:
                self.registered = True
                print(f"[✔] SIP Registered as extension {self.exten}@{self.server}")
                return True
            else:
                code2 = get_status_code(resp2) if resp2 else 0
                print(f"[SIP] Registration failed after auth. Code: {code2}")
                return False

        print(f"[SIP] Registration failed. Code: {code}")
        return False

    def send_register_refresh(self):
        """Re-register to keep the registration alive."""
        self.cseq += 1
        self.register()

    # ── INVITE (outgoing call) ─────────────────────────────────────

    def call(self, number: str) -> bool:
        """Send INVITE to FreePBX to call an external number. Returns True when answered."""
        sdp     = self._make_sdp()
        call_id = rand_call_id()
        from_tag= rand_tag()
        branch  = f"z9hG4bK{rand_tag()}"
        cseq    = 1
        to_uri  = f"sip:{number}@{self.server}"

        def _invite(auth_header=''):
            return (
                f"INVITE {to_uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch={branch}\r\n"
                f"From: <sip:{self.exten}@{self.server}>;tag={from_tag}\r\n"
                f"To: <{to_uri}>\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq} INVITE\r\n"
                f"Contact: <sip:{self.exten}@{self.local_ip}:{self.local_port}>\r\n"
                f"Max-Forwards: 70\r\n"
                f"User-Agent: PythonSIPClient/1.0\r\n"
                f"Allow: INVITE, ACK, CANCEL, OPTIONS, BYE\r\n"
                + (f"Proxy-Authorization: {auth_header}\r\n" if auth_header else '')
                + f"Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp)}\r\n\r\n"
                + sdp
            )

        self._drain_queue()
        self._send(_invite())

        remote_ip   = None
        remote_port = None
        remote_tag  = None

        deadline = time.time() + 90  # wait up to 90s for answer
        while time.time() < deadline:
            resp, addr = self._wait_response(timeout=2)
            if resp is None:
                continue
            code = get_status_code(resp)

            if code == 100 or code == 180 or code == 183:
                status = {100: 'Trying', 180: 'Ringing', 183: 'Progress'}.get(code, str(code))
                print(f"[SIP] {status}...")
                continue

            if code == 407:
                pa = get_header(resp, 'Proxy-Authenticate')
                params = parse_auth_header(pa)
                auth = md5_digest_auth(
                    self.exten, params.get('realm', self.server), self.password,
                    'INVITE', to_uri, params.get('nonce', ''),
                    qop=params.get('qop') if 'auth' in params.get('qop', '') else None
                )
                cseq += 1
                self._send(_invite(auth_header=auth))
                continue

            if code == 401:
                wwa = get_header(resp, 'WWW-Authenticate')
                params = parse_auth_header(wwa)
                auth = md5_digest_auth(
                    self.exten, params.get('realm', self.server), self.password,
                    'INVITE', to_uri, params.get('nonce', ''),
                    qop=params.get('qop') if 'auth' in params.get('qop', '') else None
                )
                cseq += 1
                self._send(_invite(auth_header=auth))
                continue

            if code == 200:
                remote_tag = get_header(resp, 'To').split('tag=')[-1] if 'tag=' in get_header(resp, 'To') else ''
                remote_ip, remote_port = self._parse_sdp_conn(resp)
                # Send ACK
                ack = (
                    f"ACK {to_uri} SIP/2.0\r\n"
                    f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch=z9hG4bK{rand_tag()}\r\n"
                    f"From: <sip:{self.exten}@{self.server}>;tag={from_tag}\r\n"
                    f"To: <{to_uri}>;tag={remote_tag}\r\n"
                    f"Call-ID: {call_id}\r\n"
                    f"CSeq: {cseq} ACK\r\n"
                    f"Max-Forwards: 70\r\n"
                    f"Content-Length: 0\r\n\r\n"
                )
                self._send(ack)
                self.peer_ip    = remote_ip
                self.peer_rtp   = remote_port
                self.remote_tag = remote_tag
                self.in_dialog  = True
                self._active_call_id = call_id
                self._active_from_tag = from_tag
                self._active_to_uri = to_uri
                return True

            if code >= 400:
                print(f"[SIP] Call rejected: {code}")
                return False

        print("[SIP] Call timed out — no answer.")
        return False

    # ── Wait for incoming INVITE ───────────────────────────────────

    def wait_for_invite(self, timeout=300) -> bool:
        """
        Block until FreePBX sends an INVITE to this extension.
        Returns True when an INVITE arrives.
        """
        print("\n[SIP] Waiting for incoming call (registered, listening)...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg, addr = self._wait_response(timeout=1)
            if msg is None:
                continue
            first_line = msg.splitlines()[0] if msg else ''
            if first_line.startswith('INVITE'):
                self.invite_msg  = msg
                self.invite_addr = addr
                # Extract caller info
                from_hdr = get_header(msg, 'From')
                print(f"\n[♫] Incoming call from: {from_hdr}")
                # Send 180 Ringing
                self._reply_to_invite(msg, addr, '180 Ringing', '')
                # Parse remote SDP
                ri, rp = self._parse_sdp_conn(msg)
                self.peer_ip  = ri
                self.peer_rtp = rp
                return True
            elif first_line.startswith('OPTIONS'):
                # Auto-reply to keepalive OPTIONS
                self._reply_options(msg, addr)
        return False

    def answer(self) -> bool:
        """Send 200 OK to accept incoming INVITE."""
        if not self.invite_msg:
            return False
        sdp = self._make_sdp()
        self._reply_to_invite(self.invite_msg, self.invite_addr, '200 OK', sdp)
        # Wait for ACK
        _, _ = self._wait_response(timeout=5)
        self.in_dialog = True
        return True

    def _reply_to_invite(self, invite_msg: str, addr, status: str, sdp: str):
        via     = get_header(invite_msg, 'Via')
        from_   = get_header(invite_msg, 'From')
        to_     = get_header(invite_msg, 'To')
        call_id = get_header(invite_msg, 'Call-ID')
        cseq    = get_header(invite_msg, 'CSeq')
        contact = f"<sip:{self.exten}@{self.local_ip}:{self.local_port}>"

        if '200' in status and 'tag=' not in to_:
            to_ += f';tag={rand_tag()}'

        ct_headers = ''
        if sdp:
            ct_headers = f"Content-Type: application/sdp\r\n"

        resp = (
            f"SIP/2.0 {status}\r\n"
            f"Via: {via}\r\n"
            f"From: {from_}\r\n"
            f"To: {to_}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Contact: {contact}\r\n"
            f"User-Agent: PythonSIPClient/1.0\r\n"
            f"{ct_headers}"
            f"Content-Length: {len(sdp)}\r\n\r\n"
            + sdp
        )
        self._send_to(resp, addr)

    def _reply_options(self, msg: str, addr):
        via     = get_header(msg, 'Via')
        from_   = get_header(msg, 'From')
        to_     = get_header(msg, 'To')
        call_id = get_header(msg, 'Call-ID')
        cseq    = get_header(msg, 'CSeq')
        resp = (
            f"SIP/2.0 200 OK\r\n"
            f"Via: {via}\r\n"
            f"From: {from_}\r\n"
            f"To: {to_}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"CSeq: {cseq}\r\n"
            f"Allow: INVITE, ACK, CANCEL, OPTIONS, BYE\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send_to(resp, addr)

    # ── BYE (hang up) ─────────────────────────────────────────────

    def hangup(self):
        """Send BYE to end the active call."""
        if not self.in_dialog:
            return
        bye = (
            f"BYE {getattr(self, '_active_to_uri', f'sip:{self.server}')} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {self.local_ip}:{self.local_port};branch=z9hG4bK{rand_tag()}\r\n"
            f"From: <sip:{self.exten}@{self.server}>;tag={getattr(self, '_active_from_tag', self.tag)}\r\n"
            f"To: <{getattr(self, '_active_to_uri', f'sip:{self.server}')}>;"
            f"tag={self.remote_tag or ''}\r\n"
            f"Call-ID: {getattr(self, '_active_call_id', self.call_id)}\r\n"
            f"CSeq: {self.cseq + 1} BYE\r\n"
            f"Max-Forwards: 70\r\n"
            f"Content-Length: 0\r\n\r\n"
        )
        self._send(bye)
        self.in_dialog = False
        print("[SIP] BYE sent.")

    def close(self):
        self._stop.set()
        self.sock.close()

    def watch_for_bye(self, callback):
        """Start a background thread that calls callback() when a BYE arrives."""
        def _watch():
            while self.in_dialog and not self._stop.is_set():
                msg, _ = self._wait_response(timeout=1)
                if msg and msg.splitlines()[0].startswith('BYE'):
                    print("\n[SIP] Remote party hung up.")
                    self.in_dialog = False
                    callback()
                    return
        threading.Thread(target=_watch, daemon=True).start()


# ──────────────────────────────────────────────────────────────────
#  RTP Handler — audio send/receive
# ──────────────────────────────────────────────────────────────────

class RTPHandler:
    def __init__(self, remote_ip: str, remote_port: int,
                 mic_frames: list, spk_frames: list):
        self.remote = (remote_ip, remote_port)
        self.mic_frames = mic_frames    # written by mic thread
        self.spk_frames = spk_frames    # written here for recording

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', RTP_LOCAL_PORT))
        self.sock.settimeout(0.05)

        self._stop     = threading.Event()
        self._seq      = random.randint(0, 0xFFFF)
        self._ts       = random.randint(0, 0xFFFFFF)
        self._ssrc     = random.randint(0, 0xFFFFFFFF)

        # Audio output stream (speaker)
        self._out_buf  = []
        self._out_lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._send_loop, daemon=True).start()
        if AUDIO_OK:
            threading.Thread(target=self._speaker_loop, daemon=True).start()
            threading.Thread(target=self._mic_loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except Exception:
            pass

    # ── Receive RTP → speaker ─────────────────────────────────────

    def _recv_loop(self):
        """Receive RTP from FreePBX and push to speaker buffer."""
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(2048)
                parsed = parse_rtp(data)
                if parsed is None:
                    continue
                pt, seq, ts, ssrc, payload = parsed
                if pt == PAYLOAD_PCMU and payload:
                    pcm = ulaw_bytes_to_pcm(payload)
                    f32 = pcm16_to_float32(pcm)
                    with self._out_lock:
                        self._out_buf.append(f32)
                    # Store for recording
                    self.spk_frames.append(f32)
            except socket.timeout:
                pass
            except Exception:
                pass

    def _speaker_loop(self):
        """Pull from buffer and play through laptop speaker."""
        try:
            with sd.OutputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                blocksize=CHUNK_FRAMES,
            ) as stream:
                while not self._stop.is_set():
                    with self._out_lock:
                        if self._out_buf:
                            chunk = self._out_buf.pop(0)
                        else:
                            chunk = np.zeros(CHUNK_FRAMES, dtype='float32')
                    stream.write(chunk.reshape(-1, 1))
        except Exception as e:
            print(f"[RTP] Speaker error: {e}")

    # ── Mic → send RTP ────────────────────────────────────────────

    def _mic_loop(self):
        """Capture mic and store frames for the send loop + recording."""
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32',
                blocksize=CHUNK_FRAMES,
            ) as stream:
                while not self._stop.is_set():
                    data, _ = stream.read(CHUNK_FRAMES)
                    f32 = data[:, 0] if data.ndim > 1 else data
                    self.mic_frames.append(f32.copy())
                    pcm = float32_to_pcm16(f32)
                    ulaw = pcm_bytes_to_ulaw(pcm)
                    with self._out_lock:
                        self._pending_ulaw = ulaw
        except Exception as e:
            print(f"[RTP] Mic error: {e}")

    def _send_loop(self):
        """Send RTP packets at regular intervals (CHUNK_MS ms)."""
        silence = bytes([0xFF] * CHUNK_FRAMES)   # μ-law silence
        self._pending_ulaw = silence
        interval = CHUNK_MS / 1000.0
        next_time = time.time()

        while not self._stop.is_set():
            payload = getattr(self, '_pending_ulaw', silence)
            pkt = make_rtp(payload, self._seq & 0xFFFF, self._ts & 0xFFFFFFFF, self._ssrc)
            try:
                self.sock.sendto(pkt, self.remote)
            except Exception:
                pass
            self._seq += 1
            self._ts  += CHUNK_FRAMES
            self._pending_ulaw = silence   # reset (mic_loop will overwrite)
            next_time += interval
            sleep = next_time - time.time()
            if sleep > 0:
                time.sleep(sleep)


# ──────────────────────────────────────────────────────────────────
#  Recording helpers
# ──────────────────────────────────────────────────────────────────

def save_wav(frames: list, path: str, label: str):
    if not AUDIO_OK or not frames:
        print(f"[!] No audio to save for {label}.")
        return
    audio = np.concatenate(frames, axis=0)
    audio = audio.reshape(-1, 1)
    sf.write(path, audio, SAMPLE_RATE)
    dur = len(audio) / SAMPLE_RATE
    print(f"[✔] Saved {label}: {path}  [{dur:.1f}s]")


# ──────────────────────────────────────────────────────────────────
#  Call log CSV
# ──────────────────────────────────────────────────────────────────

def log_call(number: str, call_type: str, duration_s: float):
    os.makedirs(os.path.dirname(CALL_LOG_CSV), exist_ok=True)
    is_new = not os.path.exists(CALL_LOG_CSV)
    with open(CALL_LOG_CSV, 'a', newline='') as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(['Timestamp', 'Number', 'Type', 'Duration(s)'])
        w.writerow([
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            number, call_type, f"{duration_s:.0f}"
        ])


# ──────────────────────────────────────────────────────────────────
#  Shared call session (used by both outgoing and incoming)
# ──────────────────────────────────────────────────────────────────

def run_session(sip: SIPClient, number: str, call_type: str):
    """Run audio and recording for an active call until user presses Enter."""
    os.makedirs(RECORDING_DIR, exist_ok=True)
    ts   = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    safe = re.sub(r'\D', '', str(number))
    path_mic = os.path.join(RECORDING_DIR, f"call_{ts}_{safe}_MIC.wav")
    path_spk = os.path.join(RECORDING_DIR, f"call_{ts}_{safe}_SPK.wav")

    mic_frames = []
    spk_frames = []

    rtp = None
    if sip.peer_ip and sip.peer_rtp:
        print(f"[RTP] Connecting audio → {sip.peer_ip}:{sip.peer_rtp}")
        rtp = RTPHandler(sip.peer_ip, sip.peer_rtp, mic_frames, spk_frames)
        rtp.start()
    else:
        print("[!] No RTP endpoint from remote — audio may not work.")

    # Watch for remote BYE
    remote_hung_up = threading.Event()
    sip.watch_for_bye(remote_hung_up.set)

    start_time = time.time()

    print("\n╔══════════════════════════════════════════════════════╗")
    print("║  CALL ACTIVE                                         ║")
    print("║  Speaker: remote voice  |  Mic: your voice           ║")
    print("║  Press  Enter  to hang up                            ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    # Wait for Enter or remote hangup
    hung = threading.Event()
    def _wait_enter():
        try:
            input()
        except Exception:
            pass
        hung.set()

    inp_thread = threading.Thread(target=_wait_enter, daemon=True)
    inp_thread.start()

    while not hung.is_set() and not remote_hung_up.is_set():
        time.sleep(0.25)

    duration = time.time() - start_time

    # Cleanup
    if rtp:
        rtp.stop()
    sip.hangup()

    # Save recordings
    print("\n[~] Saving recordings...")
    save_wav(mic_frames, path_mic, "your voice (MIC)")
    save_wav(spk_frames, path_spk, "remote voice (SPEAKER)")

    # Log call
    log_call(number, call_type, duration)
    print(f"[✔] Call logged. Duration: {duration:.0f}s")
    print(f"[✔] Recordings in: {RECORDING_DIR}")


# ──────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────

def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   FreePBX SIP Phone  (Python softphone)                 ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print(f"║  FreePBX: {FREEPBX_IP}:{FREEPBX_SIP_PORT}  •  Extension: {SIP_EXTENSION}            ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    # ── SIP registration ─────────────────────────────────────────
    print("[~] Connecting to FreePBX SIP server...")
    sip = SIPClient()

    if not sip.register():
        print("\n[!] Could not register with FreePBX. Check:")
        print(f"    • FreePBX container '{DOCKER_CONTAINER}' is running")
        print(f"    • PJSIP extension {SIP_EXTENSION} exists in FreePBX")
        print(f"    • Password '{SIP_PASSWORD}' is correct")
        print(f"    • SIP_LOCAL_PORT {SIP_LOCAL_PORT} is not blocked by firewall")
        sip.close()
        sys.exit(1)

    print(f"[✔] SIP Registered:  {SIP_EXTENSION}@{FREEPBX_IP}")

    # ── Mode ─────────────────────────────────────────────────────
    print("\nCall mode:")
    print("  1 - Outgoing call  (dial a number)")
    print("  2 - Incoming call  (wait for ring)")
    choice = input("\nChoose [1/2]: ").strip()

    try:
        # ── Mode 1: Outgoing ─────────────────────────────────────
        if choice == '1':
            if PHONE_NUMBER:
                number = str(PHONE_NUMBER)
            else:
                number = input("Enter number to dial (e.g. 9876543210): ").strip()
            if not number:
                print("[!] No number entered.")
                sys.exit(1)

            print(f"\n[→] Dialing {number} via FreePBX...")
            ok = sip.call(number)
            if not ok:
                print("[!] Call failed.")
                sip.close()
                sys.exit(1)

            print(f"[✔] Call connected to {number}")
            run_session(sip, number, 'Outgoing')

        # ── Mode 2: Incoming ─────────────────────────────────────
        elif choice == '2':
            got_call = sip.wait_for_invite(timeout=300)
            if not got_call:
                print("[~] No incoming call received. Exiting.")
                sip.close()
                sys.exit(0)

            print("[~] Answering call...")
            sip.answer()

            from_hdr = get_header(sip.invite_msg, 'From')
            # Try to extract the number
            m = re.search(r'sip:([^@>]+)@', from_hdr)
            caller = m.group(1) if m else from_hdr
            print(f"[✔] Call answered from: {caller}")
            run_session(sip, caller, 'Incoming')

        else:
            print("[!] Invalid choice.")

    except KeyboardInterrupt:
        print("\n[~] Interrupted.")
        if sip.in_dialog:
            sip.hangup()

    finally:
        sip.close()
        print("\n[~] Done.")


if __name__ == '__main__':
    main()
