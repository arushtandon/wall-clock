"""
Wall Clock Server - Live prices from investing.com
Run: python server.py
Then open: http://localhost:8080
"""

from flask import Flask, jsonify, send_file
import json
import os
import threading
import time
import re

PORT = int(os.environ.get('PORT', 8080))
app = Flask(__name__, static_folder=os.path.dirname(os.path.abspath(__file__)))

# Flag to track if background updater is running
_updater_started = False

# Global cache for prices
price_cache = {
    'data': None,
    'last_update': 0,
    'lock': threading.Lock()
}

# TradingView configuration - verified working symbols
ASSETS = {
    'silver': {
        'tv_symbol': 'TVC:SILVER',
        'symbol': 'XAG/USD',
        'name': 'Silver',
    },
    'gold': {
        'tv_symbol': 'OANDA:XAUUSD',
        'symbol': 'XAU/USD', 
        'name': 'Gold',
    },
    'sp500': {
        'tv_symbol': 'SP:SPX',
        'symbol': '^GSPC',
        'name': 'S&P 500',
    },
    'nasdaq': {
        'tv_symbol': 'NASDAQ:NDX',
        'symbol': '^IXIC',
        'name': 'Nasdaq',
    },
    'sp500_futures': {
        'tv_symbol': 'CME_MINI:ES1!',
        'symbol': 'ES',
        'name': 'S&P 500 Futures',
    },
    'nasdaq_futures': {
        'tv_symbol': 'CME_MINI:NQ1!',
        'symbol': 'NQ',
        'name': 'Nasdaq Futures',
    },
    'nifty_futures': {
        'tv_symbol': 'NSE:NIFTY1!',
        'symbol': 'NIFTY',
        'name': 'Nifty Futures',
    }
}


def fetch_from_tradingview():
    """Fetch prices from TradingView scanner API - 24/7 reliable"""
    import requests
    
    results = []
    
    # Use batch endpoint (most reliable)
    symbols = [asset['tv_symbol'] for asset in ASSETS.values()]
    url = "https://scanner.tradingview.com/global/scan"
    
    payload = {
        "symbols": {"tickers": symbols},
        "columns": ["close", "change", "change_abs"]
    }
    
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Content-Type': 'application/json',
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            
            for item in data.get('data', []):
                tv_symbol = item.get('s', '')
                values = item.get('d', [])
                
                if len(values) >= 3 and values[0]:
                    price = values[0]
                    change_pct = values[1] if values[1] else 0
                    change = values[2] if values[2] else 0
                    
                    # Find matching asset
                    for key, asset in ASSETS.items():
                        if asset['tv_symbol'] == tv_symbol:
                            results.append({
                                'symbol': asset['symbol'],
                                'regularMarketPrice': price,
                                'regularMarketChange': change,
                                'regularMarketChangePercent': change_pct,
                            })
                            print(f"  {asset['name']}: ${price:,.4f} ({change_pct:+.2f}%)", flush=True)
                            break
        else:
            print(f"TradingView HTTP {response.status_code}", flush=True)
            
    except requests.exceptions.Timeout:
        print("TradingView timeout", flush=True)
    except Exception as e:
        print(f"TradingView error: {str(e)[:100]}", flush=True)
    
    print(f"Total: {len(results)}/{len(ASSETS)}", flush=True)
    
    if results:
        return {'quoteResponse': {'result': results}}
    return None

def update_price_cache():
    """Update the price cache from TradingView"""
    global price_cache
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Fetch from TradingView
    print(f"[{timestamp}] Fetching from TradingView...", flush=True)
    data = fetch_from_tradingview()
    
    if data and data.get('quoteResponse', {}).get('result'):
        num_prices = len(data['quoteResponse']['result'])
        with price_cache['lock']:
            price_cache['data'] = data
            price_cache['last_update'] = time.time()
        print(f"[{timestamp}] Updated {num_prices} prices from TradingView", flush=True)
        return True
    else:
        print(f"[{timestamp}] Failed to fetch from TradingView, keeping last data", flush=True)
        return False

def background_price_updater():
    """Background thread that continuously updates prices 24/7"""
    consecutive_failures = 0
    
    while True:
        try:
            success = update_price_cache()
            if success:
                consecutive_failures = 0
                time.sleep(0.5)  # Update every 0.5 seconds when working
            else:
                consecutive_failures += 1
                wait_time = min(10 * consecutive_failures, 60)  # Max 1 min wait
                print(f"  Retry in {wait_time}s (failure #{consecutive_failures})", flush=True)
                time.sleep(wait_time)
                
        except Exception as e:
            consecutive_failures += 1
            print(f"Update error: {e}", flush=True)
            time.sleep(30)  # Wait 30 seconds on error
            
        # Reset after 10 failures
        if consecutive_failures >= 10:
            print("Resetting failure count, continuing...", flush=True)
            consecutive_failures = 0
            time.sleep(60)  # Wait 1 minute before retry

def start_background_updater():
    """Start the background price updater if not already running"""
    global _updater_started
    if not _updater_started:
        _updater_started = True
        print("Loading initial prices from investing.com...", flush=True)
        update_price_cache()
        updater = threading.Thread(target=background_price_updater, daemon=True)
        updater.start()
        print("Background updater started", flush=True)

# Start updater when app is imported (for gunicorn)
start_background_updater()

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'))

@app.route('/logo.png')
def logo():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png'), mimetype='image/png')

@app.route('/manifest.json')
def manifest():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifest.json'), mimetype='application/json')

@app.route('/sw.js')
def service_worker():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sw.js'), mimetype='application/javascript')

@app.route('/icon-192.png')
def icon_192():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon-192.png'), mimetype='image/png')

@app.route('/icon-512.png')
def icon_512():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon-512.png'), mimetype='image/png')

@app.route('/api/prices')
def api_prices():
    with price_cache['lock']:
        data = price_cache['data']
    
    if data:
        return jsonify(data)
    else:
        return jsonify({'error': 'Loading prices from investing.com...', 'retry': True}), 503

def get_local_ip():
    """Get the local IP address for network access"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "YOUR_IP"

if __name__ == '__main__':
    local_ip = get_local_ip()
    
    print("\n" + "="*60)
    print("          MARKET WALL CLOCK - investing.com")
    print("="*60)
    
    print(f"""
  Access the Wall Clock:
  
    This PC:        http://localhost:{PORT}
    Same Network:   http://{local_ip}:{PORT}
    
  For PUBLIC internet access, run in another terminal:
    ngrok http {PORT}
    
  Prices from: investing.com (updates every 2 seconds)
  Press Ctrl+C to stop
{"="*60}
    """, flush=True)
    
    app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False)
