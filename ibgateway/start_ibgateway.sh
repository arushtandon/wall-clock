#!/bin/bash
# IB Gateway Startup Script with IBC for Auto-Login
# This script starts IB Gateway in headless mode with automatic login

# Configuration
DISPLAY_NUM=99
TWS_MAJOR_VRSN=1030
IBC_PATH=/opt/ibc
IBC_INI=/root/ibc/config.ini
LOG_FILE=/var/log/ibgateway.log

echo "$(date): Starting IB Gateway..." >> $LOG_FILE

# Start virtual display if not running
if ! pgrep -x "Xvfb" > /dev/null; then
    echo "$(date): Starting virtual display..." >> $LOG_FILE
    Xvfb :${DISPLAY_NUM} -screen 0 1024x768x16 &
    sleep 3
fi

export DISPLAY=:${DISPLAY_NUM}

# Find IB Gateway version
IB_GATEWAY_DIR=$(ls -d /root/Jts/ibgateway/*/ 2>/dev/null | head -1)
if [ -z "$IB_GATEWAY_DIR" ]; then
    echo "$(date): ERROR: IB Gateway not found!" >> $LOG_FILE
    exit 1
fi

TWS_MAJOR_VRSN=$(basename "$IB_GATEWAY_DIR")
echo "$(date): Found IB Gateway version: $TWS_MAJOR_VRSN" >> $LOG_FILE

# Check if IBC config exists
if [ ! -f "$IBC_INI" ]; then
    echo "$(date): WARNING: IBC config not found at $IBC_INI" >> $LOG_FILE
    echo "$(date): Starting IB Gateway without auto-login..." >> $LOG_FILE
    cd "$IB_GATEWAY_DIR"
    exec ./ibgateway
else
    echo "$(date): Starting IB Gateway with IBC auto-login..." >> $LOG_FILE
    
    # Start using IBC
    cd $IBC_PATH
    exec ./gatewaystart.sh $TWS_MAJOR_VRSN -g \
        --ibc-path=$IBC_PATH \
        --ibc-ini=$IBC_INI \
        --mode=live \
        --java-path=/usr/bin/java
fi
