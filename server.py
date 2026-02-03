"""
Wall Clock Server - Live prices via Interactive Brokers
Run: python server.py
Then open: http://localhost:8080

Requires: IB Gateway running and logged in
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
IB_HOST = '127.0.0.1'
IB_PORTS = [4001, 4002, 7496, 7497]  # Common IB Gateway ports
IB_CLIENT_ID = 1

# Asset configuration
ASSETS = {
    'silver': {
        'name': 'Silver',
        'display_symbol': 'XAG/USD',
    },
    'gold': {
        'name': 'Gold',
        'display_symbol': 'XAU/USD',
    },
    'sp500': {
        'name': 'S&P 500',
        'display_symbol': '^GSPC',
    },
    'nasdaq': {
        'name': 'Nasdaq',
        'display_symbol': '^IXIC',
    },
    'sp500_futures': {
        'name': 'S&P 500 Futures',
        'display_symbol': 'ES',
    },
    'nasdaq_futures': {
        'name': 'Nasdaq Futures',
        'display_symbol': 'NQ',
    },
    'nifty_futures': {
        'name': 'Nifty Futures',
        'display_symbol': 'NIFTY',
    }
}

def get_front_month():
    """Get the front month contract date (YYYYMM) for quarterly futures (ES, NQ, GC, SI)."""
    from datetime import datetime, timedelta
    now = datetime.now()
    month, year = now.month, now.year
    quarterly_months = [3, 6, 9, 12]
    for qm in quarterly_months:
        if month <= qm:
            if now.day > 15 and month == qm:
                idx = quarterly_months.index(qm)
                return f"{year}{quarterly_months[idx+1]:02d}" if idx < 3 else f"{year+1}03"
            return f"{year}{qm:02d}"
    return f"{year+1}03"


def get_nifty_front_month():
    """Get front month for Nifty (monthly expiry, last Thursday of month). Returns YYYYMM."""
    from datetime import datetime
    now = datetime.now()
    year, month = now.year, now.month
    # Current month is front until last Thursday has passed
    return f"{year}{month:02d}"

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
                print(f"IB Gateway not reachable. Waiting 10 seconds...", flush=True)
                time.sleep(10)
                continue
            
            from ib_insync import IB, Index, Future, Forex
            
            ib = IB()
            print(f"Connecting to IB Gateway on port {connected_port}...", flush=True)
            ib.connect(IB_HOST, connected_port, clientId=IB_CLIENT_ID, timeout=20)
            ib_connected = True
            print("Connected to Interactive Brokers!", flush=True)
            
            # Get front month for futures
            front_month = get_front_month()
            print(f"Using front month: {front_month}", flush=True)
            
            # Create contracts
            from ib_insync import Contract
            contracts = {}
            
            # Gold futures (COMEX) - standard contract
            gold_contract = Future('GC', front_month, 'COMEX')
            gold_contract.multiplier = '100'
            contracts['gold'] = gold_contract
            
            # Silver futures (COMEX) - standard 5000oz contract
            silver_contract = Future('SI', front_month, 'COMEX')
            silver_contract.multiplier = '5000'
            contracts['silver'] = silver_contract
            
            # Indices
            contracts['sp500'] = Index('SPX', 'CBOE', 'USD')
            # Nasdaq: NQ futures (reliable live); we'll copy NQ price to 'nasdaq' in the loop
            contracts['nasdaq_futures'] = Future('NQ', front_month, 'CME')
            contracts['sp500_futures'] = Future('ES', front_month, 'CME')
            
            # GIFT Nifty - front month (Feb, Mar, ...), auto-roll after expiry
            nifty_front = get_nifty_front_month()  # e.g. "202602"
            print(f"Nifty front month (target): {nifty_front}", flush=True)
            nifty_found = False
            try:
                nifty_search = Contract()
                nifty_search.symbol = 'NIFTY'
                nifty_search.secType = 'FUT'
                nifty_search.exchange = 'SGX'
                nifty_search.currency = 'USD'
                matches = ib.reqContractDetails(nifty_search)
                if matches:
                    # Normalize contract month to YYYYMM for comparison
                    def norm_month(c):
                        raw = (getattr(c.contract, 'lastTradeDateOrContractMonth', '') or '').strip().replace(' ', '')
                        if len(raw) >= 6 and raw[:6].isdigit():
                            return raw[:6]
                        # e.g. "202602" or "FEB26" -> try to parse
                        from datetime import datetime
                        try:
                            if raw.isdigit():
                                return raw[:6]
                            # "FEB26" style
                            months = {'JAN':1,'FEB':2,'MAR':3,'APR':4,'MAY':5,'JUN':6,'JUL':7,'AUG':8,'SEP':9,'OCT':10,'NOV':11,'DEC':12}
                            for k, v in months.items():
                                if raw.upper().startswith(k):
                                    yr = raw[len(k):].strip()
                                    if len(yr) == 2:
                                        yr = '20' + yr
                                    return f"{yr}{v:02d}"
                        except Exception:
                            pass
                        return raw[:6] if raw else '999999'
                    matches_sorted = sorted(matches, key=lambda m: norm_month(m))
                    # Pick contract matching current front month, or nearest future
                    for m in matches_sorted:
                        cm = norm_month(m)
                        if cm == nifty_front:
                            contracts['nifty_futures'] = m.contract
                            nifty_found = True
                            print(f"Nifty using front month: {m.contract} ({getattr(m.contract, 'lastTradeDateOrContractMonth', '')})", flush=True)
                            break
                        if cm > nifty_front:
                            contracts['nifty_futures'] = m.contract
                            nifty_found = True
                            print(f"Nifty using next available: {m.contract} ({getattr(m.contract, 'lastTradeDateOrContractMonth', '')})", flush=True)
                            break
                    if not nifty_found and matches_sorted:
                        contracts['nifty_futures'] = matches_sorted[0].contract
                        nifty_found = True
                        print(f"Nifty using first listed: {matches_sorted[0].contract}", flush=True)
            except Exception as e:
                print(f"Nifty error: {e}", flush=True)
            if not nifty_found:
                print("Nifty contract not found - skipping", flush=True)
            
            # Qualify and subscribe
            tickers = {}
            ib.reqMarketDataType(1)  # 1 = live (use 3 for delayed if no subscription)
            for key, contract in contracts.items():
                try:
                    qualified = ib.qualifyContracts(contract)
                    if qualified:
                        ticker = ib.reqMktData(contract, '', False, False)
                        tickers[key] = ticker
                        print(f"Subscribed: {key} -> {contract}", flush=True)
                    else:
                        print(f"Could not qualify: {key}", flush=True)
                except Exception as e:
                    print(f"Error with {key}: {e}", flush=True)
            # If Nifty didn't qualify, try delayed data type for next connection
            if 'nifty_futures' not in tickers and contracts.get('nifty_futures'):
                print("Nifty: try enabling delayed market data in IBKR for SGX", flush=True)
            
            print(f"Streaming {len(tickers)} symbols...", flush=True)
            
            # Process updates: reconnect periodically to refresh contracts (e.g. after Nifty expiry)
            reconnect_interval = 6 * 3600  # 6 hours
            stale_threshold = 5 * 60      # 5 min without updates = force reconnect
            last_update_time = time.time()
            loop_start = time.time()
            
            while ib.isConnected():
                ib.sleep(0.1)  # Process events
                now = time.time()
                
                for key, ticker in tickers.items():
                    # Prefer live: bid/ask mid (best for futures), then last, then close
                    price = None
                    if ticker.bid and ticker.bid > 0 and ticker.ask and ticker.ask > 0:
                        price = (ticker.bid + ticker.ask) / 2
                    if (price is None or price <= 0) and ticker.last and ticker.last > 0:
                        price = ticker.last
                    if (price is None or price <= 0) and ticker.close and ticker.close > 0:
                        price = ticker.close
                    
                    if price and price > 0:
                        last_update_time = now
                        prev_close = ticker.close if ticker.close and ticker.close > 0 else price
                        change = price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close else 0
                        
                        old_price = live_prices.get(key, {}).get('price', 0)
                        if abs(price - old_price) > 0.0001:
                            data = {'price': price, 'change': change, 'change_pct': change_pct}
                            live_prices[key] = data
                            # Nasdaq row shows NQ (nasdaq_futures) price
                            if key == 'nasdaq_futures':
                                live_prices['nasdaq'] = data
                            update_price_cache_from_live()
                
                # Periodic reconnect to refresh contracts (roll to next month after expiry)
                if (now - loop_start) >= reconnect_interval:
                    print("Periodic reconnect to refresh contracts...", flush=True)
                    break
                # Reconnect if no updates for too long (connection may be stale)
                if (now - last_update_time) >= stale_threshold and price_cache['last_update']:
                    print("No price updates for 5 min - reconnecting...", flush=True)
                    break
            
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
        
        print("Starting Interactive Brokers connection...", flush=True)
        ib_thread = threading.Thread(target=run_ibkr_connection, daemon=True)
        ib_thread.start()
        print("IBKR thread started", flush=True)

# Start updater when app is imported
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
        return jsonify({'error': 'Waiting for IB Gateway connection...', 'retry': True}), 503

@app.route('/api/status')
def api_status():
    return jsonify({
        'ib_connected': ib_connected,
        'prices_count': len(live_prices),
        'last_update': price_cache['last_update']
    })

def get_local_ip():
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
    print("      MARKET WALL CLOCK - Interactive Brokers")
    print("="*60)
    
    print(f"""
  Access the Wall Clock:
  
    This PC:        http://localhost:{PORT}
    Same Network:   http://{local_ip}:{PORT}
    
  Make sure IB Gateway is running and logged in!
  Press Ctrl+C to stop
{"="*60}
    """, flush=True)
    
    app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False)
