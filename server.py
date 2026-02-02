"""
Wall Clock Server - Live prices from TradingView
Run: python server.py
Then open: http://localhost:8080
"""

from flask import Flask, jsonify, send_file
import json
import os
import threading
import time
import requests

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
    """Fetch prices from TradingView scanner API"""
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
            timeout=5,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Content-Type': 'application/json',
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            for item in data.get('data', []):
                tv_symbol = item.get('s', '')
                values = item.get('d', [])
                
                if len(values) >= 3 and values[0]:
                    for key, asset in ASSETS.items():
                        if asset['tv_symbol'] == tv_symbol:
                            results.append({
                                'symbol': asset['symbol'],
                                'regularMarketPrice': values[0],
                                'regularMarketChange': values[2] if values[2] else 0,
                                'regularMarketChangePercent': values[1] if values[1] else 0,
                            })
                            break
            
            if results:
                return {'quoteResponse': {'result': results}}
                            
    except Exception as e:
        print(f"TradingView error: {e}", flush=True)
    
    return None

def update_price_cache():
    """Update the price cache from TradingView"""
    global price_cache
    
    data = fetch_from_tradingview()
    
    if data and data.get('quoteResponse', {}).get('result'):
        with price_cache['lock']:
            price_cache['data'] = data
            price_cache['last_update'] = time.time()
        return True
    return False

def background_price_updater():
    """Background thread that continuously updates prices 24/7"""
    consecutive_failures = 0
    
    while True:
        try:
            success = update_price_cache()
            if success:
                consecutive_failures = 0
                time.sleep(1)  # Update every 1 second
            else:
                consecutive_failures += 1
                time.sleep(min(5 * consecutive_failures, 30))
                
        except Exception as e:
            consecutive_failures += 1
            time.sleep(10)
            
        if consecutive_failures >= 10:
            consecutive_failures = 0
            time.sleep(30)

def start_background_updater():
    """Start the background price updater"""
    global _updater_started
    if not _updater_started:
        _updater_started = True
        
        print("Fetching initial prices from TradingView...", flush=True)
        update_price_cache()
        
        print("Starting background price updater...", flush=True)
        updater = threading.Thread(target=background_price_updater, daemon=True)
        updater.start()
        print("Background updater started - updates every 1 second", flush=True)

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
