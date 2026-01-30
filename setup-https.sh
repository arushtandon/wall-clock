#!/bin/bash
# Setup HTTPS and remove password protection

echo "=========================================="
echo "  Setting up HTTPS (no password)"
echo "=========================================="

# Generate self-signed SSL certificate
echo "Generating SSL certificate..."
mkdir -p /etc/nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/server.key \
    -out /etc/nginx/ssl/server.crt \
    -subj "/C=US/ST=State/L=City/O=Safron/CN=45.77.46.201"

# Update Nginx config for HTTPS without password
echo "Configuring Nginx for HTTPS..."
cat > /etc/nginx/sites-available/wallclock << 'NGINXCONF'
server {
    listen 80;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name _;

    ssl_certificate /etc/nginx/ssl/server.crt;
    ssl_certificate_key /etc/nginx/ssl/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINXCONF

# Open firewall for HTTPS
echo "Opening firewall for HTTPS..."
ufw allow 443/tcp

# Restart Nginx
echo "Restarting Nginx..."
systemctl restart nginx

echo ""
echo "=========================================="
echo "  HTTPS SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "  Your Wall Clock is now available at:"
echo "  https://45.77.46.201"
echo ""
echo "  No password required!"
echo ""
echo "  NOTE: Your browser will show a security"
echo "  warning the first time. Click 'Advanced'"
echo "  then 'Proceed' to access the page."
echo "=========================================="
