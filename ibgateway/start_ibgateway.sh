#!/bin/bash
# IB Gateway 24/7 startup: Xvfb + VNC + IB Gateway (with optional IBC auto-login)
# Run this at boot so prices keep running; use noVNC to log in if not using IBC.

DISPLAY_NUM=99
LOG_FILE=/var/log/ibgateway.log

log() { echo "$(date): $1" >> "$LOG_FILE"; }

log "Starting IB Gateway 24/7..."

# 1. Virtual display
if ! pgrep -x Xvfb > /dev/null; then
    log "Starting Xvfb :${DISPLAY_NUM}"
    Xvfb :${DISPLAY_NUM} -screen 0 1024x768x16 &
    sleep 3
fi
export DISPLAY=:${DISPLAY_NUM}

# 2. VNC (so you can noVNC in to log in when needed)
if ! pgrep -x x11vnc > /dev/null; then
    log "Starting x11vnc on 5900"
    x11vnc -display :${DISPLAY_NUM} -forever -rfbport 5900 -nopw -shared -bg 2>>"$LOG_FILE"
    sleep 2
fi
if ! pgrep -f "websockify.*6080" > /dev/null; then
    log "Starting websockify on 6080"
    websockify -D --web=/usr/share/novnc/ 6080 localhost:5900 2>>"$LOG_FILE"
fi

# 3. IB Gateway path (support both /root/Jts/ibgateway and /root/Jts/ibgateway/version/)
IB_GATEWAY_EXE=""
if [ -f /root/Jts/ibgateway ]; then
    IB_GATEWAY_EXE=/root/Jts/ibgateway
    IB_GATEWAY_DIR=/root/Jts
elif [ -d /root/Jts/ibgateway ]; then
    IB_GATEWAY_DIR=$(ls -d /root/Jts/ibgateway/*/ 2>/dev/null | head -1)
    if [ -n "$IB_GATEWAY_DIR" ] && [ -x "${IB_GATEWAY_DIR}ibgateway" ]; then
        IB_GATEWAY_EXE="${IB_GATEWAY_DIR}ibgateway"
    fi
fi

if [ -z "$IB_GATEWAY_EXE" ]; then
    log "ERROR: IB Gateway not found under /root/Jts"
    exit 1
fi

# 4. Optional IBC auto-login
IBC_INI="/opt/ibc/config.ini"
if [ -f "$IBC_INI" ] && [ -x /opt/ibc/gatewaystart.sh ]; then
    log "Starting with IBC auto-login..."
    cd /opt/ibc
    # IBC expects version or path; try version from dir name or default
    VER=$(basename "$(dirname "$IB_GATEWAY_EXE")" 2>/dev/null)
    [ -z "$VER" ] && VER="1030"
    exec ./gatewaystart.sh "$VER" -g --ibc-path=/opt/ibc --ibc-ini="$IBC_INI" --mode=live --java-path=/usr/bin/java
fi

# 5. Start IB Gateway directly (log in once via noVNC)
log "Starting IB Gateway (log in via noVNC if needed)"
cd "$(dirname "$IB_GATEWAY_EXE")"
exec ./ibgateway
