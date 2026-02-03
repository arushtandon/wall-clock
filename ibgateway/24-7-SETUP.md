# IB Gateway 24/7 + Auto-Login (No Repeated Authentication)

This keeps IB Gateway running and, with IBC, logs in automatically so prices run 24/7.

---

## Avoid 2FA / Re-Authentication (Do This First)

To avoid having to log in or complete 2FA again after setup:

1. **Turn off two-factor authentication (2FA) for API login**
   - Log in at **https://www.interactivebrokers.com** → **Account Management**
   - Go to **Settings** → **User Settings** → **Security** → **Two-Factor Authentication**
   - **Disable** 2FA, or set it so that **"IB Key" / mobile 2FA is not required** for IB Gateway/API (if that option exists in your region).
   - Without 2FA, IBC can log in with just username + password in `config.ini`, so no manual step after reboot.

2. **Optional: Trust this "device"**
   - If IBKR has a "Remember this device" or "Trust this device" on the login screen, use it once when you log in via noVNC. That can reduce how often they ask for re-auth.

3. **Keep the gateway running**
   - Use the systemd service below so the gateway (and IBC) only restart when necessary. Fewer restarts = fewer logins.

**Note:** IBKR may still require a password change or re-login once in a long while for security. When that happens, use noVNC once to complete it; after that, IBC will resume auto-login.

## Option A: Auto-login with IBC (recommended)

IBC stores your credentials and logs in automatically when the gateway starts or restarts.

### 1. Install IBC on Vultr

```bash
cd /root
wget https://github.com/IbcAlpha/IBC/releases/download/3.18.0/IBCLinux-3.18.0.zip
apt-get install -y unzip
unzip -o IBCLinux-3.18.0.zip -d /opt/ibc
chmod +x /opt/ibc/*.sh /opt/ibc/scripts/*.sh
```

### 2. Create IBC config with your credentials

```bash
mkdir -p /opt/ibc
cp /root/wall-clock/ibgateway/config.ini.template /opt/ibc/config.ini
nano /opt/ibc/config.ini   # set IbLoginId and IbPassword
```

Important: set `OverrideTwsApiPort=4001` (same as your API port).

### 3. Install IB Gateway systemd service

```bash
cp /root/wall-clock/ibgateway/ibgateway.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ibgateway
systemctl start ibgateway
```

### 4. Enable wallclock to start on boot

```bash
systemctl enable wallclock
```

After a reboot, IBC will start IB Gateway and log in automatically. No need to use noVNC unless IBKR asks for 2FA or re-authentication.

---

## Option B: No IBC (manual login after each reboot)

If you don’t install IBC:

1. Install and enable the same service (steps 3–4 above).
2. After every server reboot, open **http://YOUR_SERVER_IP:6080/vnc.html**, click Connect, and log in to IB Gateway (IB API + username + password).
3. Prices will then run 24/7 until the next reboot or until IBKR ends the session.

---

---

## Automatic notifications (no need to check noVNC or IBKR)

You do **not** need to log in to noVNC or IBKR until you get a notification.

1. **When re-auth is required:** If the app loses connection to IB Gateway (e.g. session expired), it will send **one** automatic notification (throttled so you’re not spammed) to Telegram and/or email: *"IBKR re-authentication required. Log in via noVNC: …"* Then log in once via the link in the message; no need to open the IBKR website. Default: at most one alert per 24 hours. For **weekly-only** alerts, set `NOTIFY_THROTTLE_HOURS=168` in the same place as the other env vars.

2. **Weekly reminder (optional):** Run the weekly reminder once per week so you get a single reminder if something is wrong. Set the same env vars as below, then add a cron job.

### Set up Telegram (easiest) or email

On the **Vultr server**, set environment variables for the wallclock service. Create a drop-in or use systemd environment:

```bash
# Option A: Telegram (recommended - free, instant)
# 1. Create a bot: message @BotFather on Telegram, send /newbot, get token
# 2. Get your chat ID: message your bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates
mkdir -p /etc/systemd/system/wallclock.service.d
cat > /etc/systemd/system/wallclock.service.d/notify.conf << 'EOF'
[Service]
Environment=TELEGRAM_BOT_TOKEN=your_bot_token_here
Environment=TELEGRAM_CHAT_ID=your_chat_id_here
Environment=NOTIFY_NOVNC_URL=https://safronliveprices.duckdns.org/novnc/vnc.html
Environment=NOTIFY_THROTTLE_HOURS=168
EOF
# NOTIFY_THROTTLE_HOURS=168 = send at most one "re-auth required" per week
systemctl daemon-reload
systemctl restart wallclock
```

```bash
# Option B: Email (e.g. Gmail)
# Use an App Password, not your normal password: Google Account → Security → App passwords
mkdir -p /etc/systemd/system/wallclock.service.d
cat > /etc/systemd/system/wallclock.service.d/notify.conf << 'EOF'
[Service]
Environment=NOTIFY_EMAIL=you@example.com
Environment=SMTP_HOST=smtp.gmail.com
Environment=SMTP_PORT=587
Environment=SMTP_USER=you@gmail.com
Environment=SMTP_PASS=your_app_password
Environment=NOTIFY_NOVNC_URL=https://safronliveprices.duckdns.org/novnc/vnc.html
Environment=NOTIFY_THROTTLE_HOURS=168
EOF
systemctl daemon-reload
systemctl restart wallclock
```

### Weekly reminder (once per week)

Run the reminder script once per week (e.g. Sunday 9:00). It sends one message only if prices look stale.

```bash
# Make script executable
chmod +x /root/wall-clock/ibgateway/weekly-reminder.py

# Load the same env vars (or put them in /etc/environment)
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...

# Add cron: every Sunday at 9:00
crontab -e
# Add this line (use your actual path and env):
0 9 * * 0 cd /root/wall-clock && TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy /usr/bin/python3 ibgateway/weekly-reminder.py
```

Result: you only log in when you receive the automatic “re-auth required” alert or the weekly reminder. No need to check noVNC or IBKR otherwise.

---

## Notes

- **IBKR re-auth:** IBKR may still require periodic re-authentication. When you get the notification, log in once via the noVNC link; no need to open the IBKR website separately.
- **Port 4001:** The wall-clock app connects to IB Gateway on port 4001. Keep this in IBC and in IB Gateway API settings.
- **Logs:** `journalctl -u ibgateway -f` and `tail -f /var/log/ibgateway.log`
