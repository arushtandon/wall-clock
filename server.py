"""
Wall Clock Server - Live prices via Interactive Brokers
Run: python server.py
Then open: http://localhost:8080

Requires: IB Gateway running on the server
"""

from flask import Flask, jsonify, send_file
import json
import os
import threading
import time

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

# Live prices storage
live_prices = {}
ib_connected = False

# ============== IBKR Configuration ==============
# IB Gateway connection settings
IB_HOST = '127.0.0.1'  # localhost if IB Gateway runs on same server
IB_PORTS = [4002, 4001, 7496, 7497]  # Try multiple ports
IB_CLIENT_ID = 1

# Asset configuration with IBKR contract details
ASSETS = {
    'silver': {
        'symbol': 'XAGUSD',
        'secType': 'CMDTY',
        'exchange': 'SMART',
        'currency': 'USD',
        'name': 'Silver',
        'display_symbol': 'XAG/USD',
    },
    'gold': {
        'symbol': 'XAUUSD',
        'secType': 'CMDTY',
        'exchange': 'SMART',
        'currency': 'USD',
        'name': 'Gold',
        'display_symbol': 'XAU/USD',
    },
    'sp500': {
        'symbol': 'SPX',
        'secType': 'IND',
        'exchange': 'CBOE',
        'currency': 'USD',
        'name': 'S&P 500',
        'display_symbol': '^GSPC',
    },
    'nasdaq': {
        'symbol': 'NDX',
        'secType': 'IND',
        'exchange': 'NASDAQ',
        'currency': 'USD',
        'name': 'Nasdaq',
        'display_symbol': '^IXIC',
    },
    'sp500_futures': {
        'symbol': 'ES',
        'secType': 'FUT',
        'exchange': 'CME',
        'currency': 'USD',
        'lastTradeDateOrContractMonth': '',  # Will be set dynamically
        'name': 'S&P 500 Futures',
        'display_symbol': 'ES',
    },
    'nasdaq_futures': {
        'symbol': 'NQ',
        'secType': 'FUT',
        'exchange': 'CME',
        'currency': 'USD',
        'lastTradeDateOrContractMonth': '',  # Will be set dynamically
        'name': 'Nasdaq Futures',
        'display_symbol': 'NQ',
    },
    'nifty_futures': {
        'symbol': 'NIFTY50',
        'secType': 'FUT',
        'exchange': 'NSE',
        'currency': 'INR',
        'lastTradeDateOrContractMonth': '',  # Will be set dynamically
        'name': 'Nifty Futures',
        'display_symbol': 'NIFTY',
    }
}

def get_front_month():
    """Get the front month contract date (YYYYMM format)"""
    from datetime import datetime, timedelta
    now = datetime.now()
    # Futures typically roll on 3rd Friday, so use next month if past 15th
    if now.day > 15:
        now = now.replace(day=1) + timedelta(days=32)
    return now.strftime('%Y%m')

def update_price_cache_from_live():
    """Update the Flask cache from live prices"""
    global live_prices, price_cache
    
    results = []
    for key, asset in ASSETS.items():
        if key in live_prices:
            data = live_prices[key]
            results.append({
                'symbol': asset['display_symbol'],
                'regularMarketPrice': data.get('price', 0),
                'regularMarketChange': data.get('change', 0),
                'regularMarketChangePercent': data.get('change_pct', 0),
            })
    
    if results:
        with price_cache['lock']:
            price_cache['data'] = {'quoteResponse': {'result': results}}
            price_cache['last_update'] = time.time()

def run_ibkr_connection():
    """Run IBKR connection using ib_insync for real-time prices"""
    global live_prices, ib_connected
    
    import asyncio
    import socket
    
    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    while True:
        ib = None
        try:
            # Try multiple ports to find IB Gateway
            connected_port = None
            for port in IB_PORTS:
                print(f"Checking IB Gateway at {IB_HOST}:{port}...", flush=True)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                result = sock.connect_ex((IB_HOST, port))
                sock.close()
                
                if result == 0:
                    connected_port = port
                    print(f"IB Gateway found on port {port}!", flush=True)
                    break
            
            if not connected_port:
                print(f"IB Gateway not reachable on any port {IB_PORTS}. Is it running?", flush=True)
                print("Waiting 30 seconds before retry...", flush=True)
                time.sleep(30)
                continue
            
            from ib_insync import IB, Index, Future, Forex
            
            ib = IB()
            ib.connect(IB_HOST, connected_port, clientId=IB_CLIENT_ID, timeout=15)
            ib_connected = True
            print("Connected to Interactive Brokers!", flush=True)
            
            # Get front month for futures
            front_month = get_front_month()
            print(f"Using front month: {front_month}", flush=True)
            
            # Create contracts
            contracts = {}
            
            # Silver & Gold as Forex pairs
            contracts['silver'] = Forex('XAGUSD')
            contracts['gold'] = Forex('XAUUSD')
            
            # Indices
            contracts['sp500'] = Index('SPX', 'CBOE', 'USD')
            contracts['nasdaq'] = Index('NDX', 'NASDAQ', 'USD')
            
            # Futures
            contracts['sp500_futures'] = Future('ES', front_month, 'CME')
            contracts['nasdaq_futures'] = Future('NQ', front_month, 'CME')
            contracts['nifty_futures'] = Future('NIFTY50', front_month, 'SGX')
            
            # Qualify and subscribe
            tickers = {}
            for key, contract in contracts.items():
                try:
                    qualified = ib.qualifyContracts(contract)
                    if qualified:
                        ticker = ib.reqMktData(contract, '', False, False)
                        tickers[key] = ticker
                        print(f"Subscribed: {key}", flush=True)
                    else:
                        print(f"Could not qualify: {key}", flush=True)
                except Exception as e:
                    print(f"Error with {key}: {e}", flush=True)
            
            print(f"Streaming {len(tickers)} symbols...", flush=True)
            
            # Process updates
            while ib.isConnected():
                ib.sleep(0.1)
                
                for key, ticker in tickers.items():
                    price = None
                    if ticker.last and ticker.last > 0:
                        price = ticker.last
                    elif ticker.close and ticker.close > 0:
                        price = ticker.close
                    elif ticker.bid and ticker.bid > 0 and ticker.ask and ticker.ask > 0:
                        price = (ticker.bid + ticker.ask) / 2
                    
                    if price and price > 0:
                        prev_close = ticker.close if ticker.close and ticker.close > 0 else price
                        change = price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close else 0
                        
                        old_price = live_prices.get(key, {}).get('price', 0)
                        if abs(price - old_price) > 0.0001:
                            live_prices[key] = {
                                'price': price,
                                'change': change,
                                'change_pct': change_pct,
                            }
                            update_price_cache_from_live()
                            print(f"  {ASSETS[key]['name']}: {price:.4f}", flush=True)
            
            print("IB connection lost", flush=True)
            ib_connected = False
            
        except Exception as e:
            print(f"IBKR error: {type(e).__name__}: {e}", flush=True)
            ib_connected = False
        
        # Cleanup
        if ib:
            try:
                ib.disconnect()
            except:
                pass
        
        print("Retrying in 10 seconds...", flush=True)
        time.sleep(10)

def start_background_updater():
    """Start the IBKR connection"""
    global _updater_started
    if not _updater_started:
        _updater_started = True
        
        print("Starting Interactive Brokers connection for real-time prices...", flush=True)
        ib_thread = threading.Thread(target=run_ibkr_connection, daemon=True)
        ib_thread.start()
        print("IBKR thread started", flush=True)

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

@app.route('/screen.png')
def screen():
    if os.path.exists('/tmp/screen.png'):
        return send_file('/tmp/screen.png', mimetype='image/png')
    return "No screenshot available", 404

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
