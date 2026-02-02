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


import random
import string
import requests

# Global WebSocket price cache for real-time updates
ws_prices = {}
ws_connected = False
ws_instance = None

def generate_session():
    """Generate a random session ID for TradingView WebSocket"""
    return 'qs_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))

def create_tv_message(method, params):
    """Create a properly formatted TradingView WebSocket message"""
    import json
    msg = json.dumps({"m": method, "p": params})
    return f"~m~{len(msg)}~m~{msg}"

def parse_tv_message(message):
    """Parse TradingView WebSocket messages"""
    import json
    results = []
    parts = message.split('~m~')
    for part in parts:
        if part and not part.isdigit():
            try:
                if part.startswith('{'):
                    results.append(json.loads(part))
            except:
                pass
    return results

def start_websocket():
    """Start TradingView WebSocket connection for real-time data"""
    global ws_prices, ws_connected, ws_instance
    
    while True:  # Auto-reconnect loop
        try:
            import websocket
            
            session = generate_session()
            
            def on_message(ws, message):
                global ws_prices
                try:
                    # Handle ping/pong
                    if '~h~' in message:
                        ws.send(message)
                        return
                    
                    msgs = parse_tv_message(message)
                    for data in msgs:
                        if data.get('m') == 'qsd':
                            p = data.get('p', [])
                            if len(p) >= 2 and isinstance(p[1], dict):
                                symbol = p[1].get('n', '')
                                values = p[1].get('v', {})
                                
                                price = values.get('lp')
                                if price and price > 0:
                                    ws_prices[symbol] = {
                                        'price': price,
                                        'change': values.get('ch', 0) or 0,
                                        'change_pct': values.get('chp', 0) or 0,
                                    }
                                    print(f"  WS: {symbol} = {price}", flush=True)
                except Exception as e:
                    pass
            
            def on_error(ws, error):
                global ws_connected
                ws_connected = False
                print(f"WebSocket error: {error}", flush=True)
            
            def on_close(ws, close_status_code, close_msg):
                global ws_connected
                ws_connected = False
                print("WebSocket closed", flush=True)
            
            def on_open(ws):
                global ws_connected
                ws_connected = True
                print("WebSocket connected!", flush=True)
                
                try:
                    # Auth token
                    ws.send(create_tv_message('set_auth_token', ['unauthorized_user_token']))
                    
                    # Create quote session
                    ws.send(create_tv_message('quote_create_session', [session]))
                    
                    # Set fields to receive
                    fields = ['lp', 'ch', 'chp', 'volume', 'bid', 'ask']
                    ws.send(create_tv_message('quote_set_fields', [session] + fields))
                    
                    # Add symbols
                    for asset in ASSETS.values():
                        symbol = asset['tv_symbol']
                        ws.send(create_tv_message('quote_add_symbols', [session, symbol]))
                    
                    print(f"Subscribed to {len(ASSETS)} symbols", flush=True)
                except Exception as e:
                    print(f"Subscription error: {e}", flush=True)
            
            ws_url = "wss://data.tradingview.com/socket.io/websocket"
            ws_instance = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                header={
                    'Origin': 'https://www.tradingview.com',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )
            
            ws_instance.run_forever(ping_interval=30, ping_timeout=10)
            
        except Exception as e:
            print(f"WebSocket error: {e}", flush=True)
        
        ws_connected = False
        print("Reconnecting in 5 seconds...", flush=True)
        time.sleep(5)

def fetch_from_tradingview():
    """Get prices from WebSocket cache or scanner API"""
    global ws_prices, ws_connected
    
    results = []
    
    # Check WebSocket cache first (for real-time data)
    if ws_connected and ws_prices:
        for key, asset in ASSETS.items():
            tv_symbol = asset['tv_symbol']
            if tv_symbol in ws_prices:
                data = ws_prices[tv_symbol]
                results.append({
                    'symbol': asset['symbol'],
                    'regularMarketPrice': data['price'],
                    'regularMarketChange': data['change'],
                    'regularMarketChangePercent': data['change_pct'],
                })
        
        if len(results) >= 4:
            print(f"Using WebSocket data: {len(results)} prices", flush=True)
            return {'quoteResponse': {'result': results}}
    
    # Fallback to scanner API
    print("Using scanner API fallback...", flush=True)
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
            
            print(f"Scanner API: {len(results)} prices", flush=True)
        else:
            print(f"Scanner API HTTP {response.status_code}", flush=True)
                            
    except Exception as e:
        print(f"Scanner API error: {e}", flush=True)
    
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
    global ws_connected
    consecutive_failures = 0
    
    while True:
        try:
            success = update_price_cache()
            if success:
                consecutive_failures = 0
                # If WebSocket is connected, we have real-time data
                # Just refresh cache more slowly since WebSocket pushes updates
                if ws_connected:
                    time.sleep(0.1)  # 100ms - just cache refresh
                else:
                    time.sleep(0.5)  # 500ms - scanner API polling
            else:
                consecutive_failures += 1
                wait_time = min(5 * consecutive_failures, 30)
                print(f"  Retry in {wait_time}s (failure #{consecutive_failures})", flush=True)
                time.sleep(wait_time)
                
        except Exception as e:
            consecutive_failures += 1
            print(f"Update error: {e}", flush=True)
            time.sleep(10)
            
        # Reset after 10 failures
        if consecutive_failures >= 10:
            print("Resetting failure count, continuing...", flush=True)
            consecutive_failures = 0
            time.sleep(30)

def start_background_updater():
    """Start the background price updater and WebSocket connection"""
    global _updater_started
    if not _updater_started:
        _updater_started = True
        
        # First, get initial prices via scanner API (fast, reliable)
        print("Fetching initial prices via scanner API...", flush=True)
        update_price_cache()
        
        # Start WebSocket connection for real-time data
        print("Starting TradingView WebSocket connection...", flush=True)
        ws_thread = threading.Thread(target=start_websocket, daemon=True)
        ws_thread.start()
        
        # Start price cache updater
        print("Starting background price updater...", flush=True)
        updater = threading.Thread(target=background_price_updater, daemon=True)
        updater.start()
        print("All background services started", flush=True)

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
