#!/bin/bash
# Setup Let's Encrypt SSL for safronliveprices.duckdns.org

DOMAIN="safronliveprices.duckdns.org"

echo "=========================================="
echo "  Setting up Trusted SSL Certificate"
echo "  Domain: $DOMAIN"
echo "=========================================="

# Install certbot
echo "Installing Certbot..."
apt update
apt install -y certbot python3-certbot-nginx

# Update Nginx config for the domain
echo "Configuring Nginx..."
cat > /etc/nginx/sites-available/wallclock << NGINXCONF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXCONF

# Restart Nginx
systemctl restart nginx

# Get SSL certificate
echo "Obtaining SSL certificate..."
certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN --redirect

# Restart Nginx again
systemctl restart nginx

echo ""
echo "=========================================="
echo "  SSL SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "  Your Wall Clock is now available at:"
echo "  https://$DOMAIN"
echo ""
echo "  NO security warnings!"
echo "  Certificate auto-renews every 90 days."
echo "=========================================="
