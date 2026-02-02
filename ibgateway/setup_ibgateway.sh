#!/bin/bash
# Complete IB Gateway Setup Script for Vultr Server
# Run this script once to set up everything

set -e

echo "=========================================="
echo "IB Gateway 24/7 Setup Script"
echo "=========================================="

# Install dependencies
echo "Installing dependencies..."
apt-get update
apt-get install -y xvfb unzip wget default-jre

# Download and install IB Gateway if not already installed
if [ ! -d "/root/Jts/ibgateway" ]; then
    echo "Downloading IB Gateway..."
    cd /root
    wget -q https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh
    chmod +x ibgateway-stable-standalone-linux-x64.sh
    echo "Installing IB Gateway (this may take a few minutes)..."
    ./ibgateway-stable-standalone-linux-x64.sh -q
    rm ibgateway-stable-standalone-linux-x64.sh
    echo "IB Gateway installed!"
else
    echo "IB Gateway already installed"
fi

# Download and install IBC (IB Controller) for auto-login
if [ ! -d "/opt/ibc" ]; then
    echo "Downloading IBC (IB Controller)..."
    cd /opt
    wget -q https://github.com/IbcAlpha/IBC/releases/download/3.18.0/IBCLinux-3.18.0.zip
    unzip -q IBCLinux-3.18.0.zip -d ibc
    rm IBCLinux-3.18.0.zip
    chmod +x /opt/ibc/*.sh
    echo "IBC installed!"
else
    echo "IBC already installed"
fi

# Create IBC config directory
mkdir -p /root/ibc

# Copy scripts to wall-clock directory
echo "Setting up startup scripts..."
mkdir -p /root/wall-clock/ibgateway
cp /root/wall-clock/ibgateway/start_ibgateway.sh /root/wall-clock/ibgateway/ 2>/dev/null || true
chmod +x /root/wall-clock/ibgateway/start_ibgateway.sh

# Install systemd service
echo "Installing systemd service..."
cp /root/wall-clock/ibgateway/ibgateway.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ibgateway

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. Create IBC config file with your credentials:"
echo "   nano /root/ibc/config.ini"
echo ""
echo "   Add these lines (replace with your credentials):"
echo "   IbLoginId=YOUR_USERNAME"
echo "   IbPassword=YOUR_PASSWORD"
echo "   TradingMode=live"
echo "   AcceptIncomingConnectionAction=accept"
echo "   AcceptNonBrokerageAccountWarning=yes"
echo ""
echo "2. Start IB Gateway:"
echo "   systemctl start ibgateway"
echo ""
echo "3. Check status:"
echo "   systemctl status ibgateway"
echo "   tail -f /var/log/ibgateway.log"
echo ""
echo "4. Restart wall clock:"
echo "   systemctl restart wallclock"
echo ""
