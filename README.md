# Market Wall Clock

A beautiful wall clock displaying live prices for Silver, Gold, S&P 500, and Nasdaq - designed for PC, iPhone, and Android.

## Features

- **Real-time clock** with elegant display
- **Live market prices** from investing.com (updates every 0.5 seconds)
- **Price change indicators** with color-coded up/down arrows
- **Click to expand** - tap any asset to make it larger
- **Responsive design** - works on desktop, tablet, and mobile
- **Fullscreen mode** - perfect for wall displays
- **Auto-hide cursor** after inactivity
- **Dark theme** - easy on the eyes

## Quick Start

### Step 1: Start the Server

```bash
cd wall-clock
python server.py
```

Or double-click `start-clock.bat`

### Step 2: Access the Clock

| Location | URL |
|----------|-----|
| **This PC** | http://localhost:8080 |
| **Same Network** | http://YOUR_IP:8080 |
| **Anywhere (Public)** | See below |

## Access from ANY Device, ANY Location

### Option 1: Same Network (Home/Office WiFi)

Devices on the same WiFi can access via your local IP:
- The server shows your IP when it starts
- Example: `http://10.0.0.16:8080`

### Option 2: Public Internet Access (Anywhere)

Use ngrok to create a public URL accessible from anywhere in the world:

1. **First time only - Set up ngrok:**
   - Go to https://dashboard.ngrok.com/signup (FREE)
   - Copy your authtoken from the dashboard
   - Run: `ngrok config add-authtoken YOUR_TOKEN`

2. **Start the public link:**
   - Double-click `start-public.bat`
   - Or run: `ngrok http 8080`
   - Copy the `https://xxxx.ngrok-free.app` URL
   - Share this URL with anyone!

## Mobile Setup

### iPhone / iPad

1. Start the server on your PC
2. Open Safari and go to the URL
3. Tap Share → "Add to Home Screen"
4. Open from home screen for full-screen

### Android

1. Start the server on your PC
2. Open Chrome and go to the URL
3. Tap menu (⋮) → "Add to Home screen"
4. Open from home screen for full-screen

## Assets Displayed

| Asset | Symbol | Source |
|-------|--------|--------|
| Silver Spot | XAG/USD | investing.com |
| Gold Spot | XAU/USD | investing.com |
| S&P 500 | ^GSPC | investing.com |
| Nasdaq 100 | ^NDX | investing.com |

## Customization

### Change Update Frequency
In `index.html`, find this line:
```javascript
setInterval(updatePrices, 500); // Update every 0.5 seconds
```

## Troubleshooting

### Prices not loading
- Ensure Python server is running
- Check your internet connection
- Some corporate networks block external requests

### Phone can't connect (local network)
- Ensure phone and PC are on the same WiFi
- Check Windows Firewall allows Python through
- Use ngrok for guaranteed access

### ngrok not working
- Make sure you signed up and added your authtoken
- Restart the terminal after installing ngrok

## Technical Details

- **Data Source:** investing.com
- **Update Interval:** 0.5 seconds (client), 2 seconds (server fetch)
- **Backend:** Python Flask with curl_cffi
- **Supported Browsers:** Chrome, Firefox, Safari, Edge

## Files

| File | Purpose |
|------|---------|
| `server.py` | Python server that fetches prices |
| `index.html` | The wall clock interface |
| `start-clock.bat` | Quick start script (Windows) |
| `start-public.bat` | Create public URL with ngrok |

## License

Free for personal use.
