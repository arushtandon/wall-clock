"""
Wall Clock Server - Live prices from TradingView WebSocket
Run: python server.py
Then open: http://localhost:8080
"""

from flask import Flask, jsonify, send_file
import json
import os
import threading
import time
import random
import string

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

# WebSocket state
ws_connected = False
ws_prices = {}

# TradingView configuration
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

# ============== TradingView WebSocket ==============

def generate_session_id():
    """Generate a random session ID"""
    return 'qs_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))

def create_message(method, params):
    """Create a TradingView protocol message"""
    msg = json.dumps({"m": method, "p": params})
    return f"~m~{len(msg)}~m~{msg}"

def parse_messages(raw):
    """Parse TradingView protocol messages"""
    messages = []
    parts = raw.split('~m~')
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.isdigit():
            i += 1
            if i < len(parts):
                try:
                    messages.append(json.loads(parts[i]))
                except:
                    pass
        i += 1
    return messages

def update_price_from_ws(symbol, price, change, change_pct):
    """Update price cache immediately when WebSocket receives data"""
    global ws_prices, price_cache
    
    ws_prices[symbol] = {
        'price': price,
        'change': change or 0,
        'change_pct': change_pct or 0,
    }
    
    # Build results from all cached prices
    results = []
    for key, asset in ASSETS.items():
        tv_sym = asset['tv_symbol']
        if tv_sym in ws_prices:
            data = ws_prices[tv_sym]
            results.append({
                'symbol': asset['symbol'],
                'regularMarketPrice': data['price'],
                'regularMarketChange': data['change'],
                'regularMarketChangePercent': data['change_pct'],
            })
    
    if results:
        with price_cache['lock']:
            price_cache['data'] = {'quoteResponse': {'result': results}}
            price_cache['last_update'] = time.time()

def run_websocket():
    """Run TradingView WebSocket connection with auto-reconnect"""
    global ws_connected
    
    import websocket
    
    while True:
        try:
            session_id = generate_session_id()
            print(f"Connecting to TradingView WebSocket...", flush=True)
            
            def on_message(ws, message):
                global ws_connected
                
                # Handle heartbeat
                if '~h~' in message:
                    ws.send(message)
                    return
                
                # Parse messages
                for msg in parse_messages(message):
                    m_type = msg.get('m')
                    
                    if m_type == 'qsd':
                        # Quote data update
                        p = msg.get('p', [])
                        if len(p) >= 2 and isinstance(p[1], dict):
                            symbol = p[1].get('n', '')
                            v = p[1].get('v', {})
                            
                            price = v.get('lp')  # Last price
                            if price and price > 0:
                                change = v.get('ch', 0)
                                change_pct = v.get('chp', 0)
                                update_price_from_ws(symbol, price, change, change_pct)
                                print(f"  {symbol}: {price:.4f} ({change_pct:+.2f}%)", flush=True)
            
            def on_error(ws, error):
                global ws_connected
                ws_connected = False
                print(f"WebSocket error: {error}", flush=True)
            
            def on_close(ws, code, msg):
                global ws_connected
                ws_connected = False
                print(f"WebSocket closed: {code} {msg}", flush=True)
            
            def on_open(ws):
                global ws_connected
                ws_connected = True
                print("WebSocket connected!", flush=True)
                
                # Authenticate with TradingView Premium session
                auth_token = 'ul9ljpf31sb5azqquo0kuejtl2700x1m'
                ws.send(create_message('set_auth_token', [auth_token]))
                print("Authenticated with premium session", flush=True)
                
                # Create quote session
                ws.send(create_message('quote_create_session', [session_id]))
                
                # Set fields we want to receive
                fields = ['lp', 'ch', 'chp', 'high_price', 'low_price', 'volume', 'bid', 'ask']
                ws.send(create_message('quote_set_fields', [session_id] + fields))
                
                # Subscribe to all symbols
                for asset in ASSETS.values():
                    symbol = asset['tv_symbol']
                    ws.send(create_message('quote_add_symbols', [session_id, symbol]))
                    print(f"Subscribed to {symbol}", flush=True)
            
            # Connect
            ws = websocket.WebSocketApp(
                "wss://data.tradingview.com/socket.io/websocket",
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                header={
                    'Origin': 'https://www.tradingview.com',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )
            
            ws.run_forever(ping_interval=25, ping_timeout=10)
            
        except Exception as e:
            print(f"WebSocket exception: {e}", flush=True)
        
        ws_connected = False
        print("Reconnecting in 3 seconds...", flush=True)
        time.sleep(3)

def start_background_updater():
    """Start the WebSocket connection"""
    global _updater_started
    if not _updater_started:
        _updater_started = True
        
        print("Starting TradingView WebSocket for real-time prices...", flush=True)
        ws_thread = threading.Thread(target=run_websocket, daemon=True)
        ws_thread.start()
        print("WebSocket thread started", flush=True)

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
