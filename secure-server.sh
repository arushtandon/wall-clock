#!/bin/bash
# Secure Wall Clock with Password Protection

echo "=========================================="
echo "  Securing Wall Clock with Password"
echo "=========================================="

# Install required packages
echo "Installing nginx and utilities..."
apt update
apt install -y nginx apache2-utils

# Create password file (username: safron, password: LivePrices2026)
echo "Creating password file..."
htpasswd -cb /etc/nginx/.htpasswd safron LivePrices2026

# Create Nginx config
echo "Configuring Nginx..."
cat > /etc/nginx/sites-available/wallclock << 'NGINXCONF'
server {
    listen 80;
    server_name _;

    auth_basic "Safron Wall Clock";
    auth_basic_user_file /etc/nginx/.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
NGINXCONF

# Enable site
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/wallclock /etc/nginx/sites-enabled/

# Update wallclock service to listen on localhost only
echo "Updating wallclock service..."
sed -i 's/0.0.0.0:80/127.0.0.1:8080/g' /etc/systemd/system/wallclock.service
sed -i 's/0.0.0.0:\$PORT/127.0.0.1:8080/g' /etc/systemd/system/wallclock.service

# Restart services
echo "Restarting services..."
systemctl daemon-reload
systemctl restart wallclock
systemctl restart nginx

# Test
sleep 2
echo ""
echo "=========================================="
echo "  SECURITY SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "  Your Wall Clock is now password protected!"
echo ""
echo "  URL: http://45.77.46.201"
echo "  Username: safron"
echo "  Password: LivePrices2026"
echo ""
echo "=========================================="
