# IB Gateway 24/7 + Auto-Login (No Repeated Authentication)

This keeps IB Gateway running and, with IBC, logs in automatically so prices run 24/7.

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

## Notes

- **IBKR re-auth:** IBKR may still require periodic re-authentication (e.g. 2FA or password). When that happens, use noVNC to complete it; IBC will handle normal restarts.
- **Port 4001:** The wall-clock app connects to IB Gateway on port 4001. Keep this in IBC and in IB Gateway API settings.
- **Logs:** `journalctl -u ibgateway -f` and `tail -f /var/log/ibgateway.log`
