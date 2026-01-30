#!/bin/bash
# Wall Clock Vultr Setup Script
# Run this on your Vultr server

set -e

echo "=========================================="
echo "  Setting up Wall Clock on Vultr"
echo "=========================================="

# Update system
echo "Updating system..."
apt update
apt install -y python3 python3-pip python3-venv git

# Clone repository
echo "Cloning repository..."
cd /root
rm -rf wall-clock
git clone https://github.com/arushtandon/wall-clock.git
cd wall-clock

# Setup Python environment
echo "Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install flask gunicorn requests beautifulsoup4 curl_cffi

# Create systemd service
echo "Creating systemd service..."
cat > /etc/systemd/system/wallclock.service << 'SERVICEEOF'
[Unit]
Description=Wall Clock Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/wall-clock
Environment=PATH=/root/wall-clock/venv/bin:/usr/bin:/bin
ExecStart=/root/wall-clock/venv/bin/gunicorn server:app --bind 0.0.0.0:80 --workers 1 --threads 2
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Enable and start service
echo "Starting service..."
systemctl daemon-reload
systemctl enable wallclock
systemctl start wallclock

# Configure firewall
echo "Configuring firewall..."
ufw allow 80/tcp
ufw allow 22/tcp
ufw --force enable

# Get server IP
SERVER_IP=$(curl -s ifconfig.me)

echo ""
echo "=========================================="
echo "  SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "  Your Wall Clock is now live at:"
echo "  http://$SERVER_IP"
echo ""
echo "  It will run 24/7 automatically!"
echo "=========================================="
