# =============================================================
# Phone Call Controller - Docker Image
# Uses ADB + scrcpy to make GSM calls via Android phone
# =============================================================
FROM ubuntu:22.04

LABEL maintainer="RandR Phone Call Controller"
LABEL description="Make GSM/cellular calls from laptop via Android phone (ADB + scrcpy)"

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# -----------------------------------------------------------------
# 1. Install system dependencies
# -----------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # ADB & USB
    android-tools-adb \
    usbutils \
    # scrcpy & audio
    scrcpy \
    pulseaudio-utils \
    ffmpeg \
    # PowerShell Core prerequisites
    wget \
    apt-transport-https \
    software-properties-common \
    ca-certificates \
    curl \
    gnupg \
    # Networking
    iproute2 \
    iputils-ping \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------
# 2. Install PowerShell Core (7.x)
# -----------------------------------------------------------------
RUN wget -q "https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb" \
    -O /tmp/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends powershell \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------
# 3. Create app directory and user
# -----------------------------------------------------------------
RUN useradd -ms /bin/bash phoneuser \
    && mkdir -p /app/data \
    && chown -R phoneuser:phoneuser /app

WORKDIR /app

# -----------------------------------------------------------------
# 4. Copy application scripts
# -----------------------------------------------------------------
COPY --chown=phoneuser:phoneuser call.ps1 /app/call.ps1
COPY --chown=phoneuser:phoneuser setup.ps1 /app/setup.ps1
COPY --chown=phoneuser:phoneuser README.md /app/README.md
COPY --chown=phoneuser:phoneuser entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

# -----------------------------------------------------------------
# 5. Persistent data volume (contacts, call log)
# -----------------------------------------------------------------
VOLUME ["/app/data"]

# -----------------------------------------------------------------
# 6. Environment variables for configuration
# -----------------------------------------------------------------
# Phone IP for WiFi ADB connection (required in Docker since USB passthrough is limited)
ENV PHONE_IP=""
# ADB port (default 5555)
ENV ADB_PORT=5555
# Auto-dial number on startup (optional)
ENV DIAL_NUMBER=""
# Enable laptop audio forwarding via scrcpy
ENV AUDIO_FORWARD=true
# ADB home for keys
ENV ADB_VENDOR_KEYS=/root/.android/adbkey
ENV HOME=/root

# -----------------------------------------------------------------
# Healthcheck: verify ADB can reach the phone
# -----------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD adb devices 2>/dev/null | grep -q "device$" || exit 1

# -----------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["interactive"]
