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

# Investing.com configuration
ASSETS = {
    'silver': {
        'pair_id': '8836',
        'symbol': 'XAG/USD',
        'name': 'Silver Spot',
        'url': 'https://www.investing.com/currencies/xag-usd'
    },
    'gold': {
        'pair_id': '8830',
        'symbol': 'XAU/USD', 
        'name': 'Gold Spot',
        'url': 'https://www.investing.com/currencies/xau-usd'
    },
    'sp500': {
        'pair_id': '166',
        'symbol': '^GSPC',
        'name': 'S&P 500',
        'url': 'https://www.investing.com/indices/us-spx-500'
    },
    'nasdaq': {
        'pair_id': '20',
        'symbol': '^NDX',
        'name': 'Nasdaq 100',
        'url': 'https://www.investing.com/indices/nq-100'
    },
    'sp500_futures': {
        'pair_id': '8839',
        'symbol': 'ES',
        'name': 'S&P 500 Futures',
        'url': 'https://www.investing.com/indices/us-spx-500-futures'
    },
    'nasdaq_futures': {
        'pair_id': '8874',
        'symbol': 'NQ',
        'name': 'Nasdaq Futures',
        'url': 'https://www.investing.com/indices/nq-100-futures'
    },
    'nifty_futures': {
        'pair_id': '101817',
        'symbol': 'NIFTY',
        'name': 'Nifty Futures',
        'url': 'https://www.investing.com/indices/india-50-futures'
    }
}

def fetch_from_investing_api():
    """Fetch live prices from investing.com using their internal API"""
    try:
        from curl_cffi import requests
    except ImportError:
        print("curl_cffi not installed! Run: pip install curl_cffi", flush=True)
        return None
    
    results = []
    pair_ids = [asset['pair_id'] for asset in ASSETS.values()]
    
    # Try the quotes refresh endpoint
    try:
        # This endpoint is used by investing.com for real-time updates
        url = "https://api.investing.com/api/financialdata/assets/equitiesByCountry/6"
        
        response = requests.get(
            url,
            impersonate="chrome",
            timeout=10,
            headers={
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Origin': 'https://www.investing.com',
                'Referer': 'https://www.investing.com/',
            }
        )
        
        if response.status_code == 200:
            print(f"API response received", flush=True)
    except Exception as e:
        print(f"API test failed: {e}", flush=True)
    
    # Fetch each asset individually using the streaming endpoint
    for key, asset in ASSETS.items():
        try:
            # Use the chart API which returns current price
            url = f"https://api.investing.com/api/financialdata/{asset['pair_id']}/historical/chart/"
            params = "?period=P1D&interval=PT1M&pointscount=1"
            
            response = requests.get(
                url + params,
                impersonate="chrome",
                timeout=10,
                headers={
                    'Accept': 'application/json',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Origin': 'https://www.investing.com',
                    'Referer': 'https://www.investing.com/',
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if 'data' in data and len(data['data']) > 0:
                    latest = data['data'][-1]
                    
                    # Try different field names
                    price = (latest.get('last_close') or 
                            latest.get('close') or 
                            latest.get('last') or
                            latest.get('y') or 0)
                    
                    open_price = latest.get('open') or latest.get('last_open') or price
                    
                    if price and price > 0:
                        change = price - open_price if open_price else 0
                        change_pct = (change / open_price * 100) if open_price and open_price != 0 else 0
                        
                        results.append({
                            'symbol': asset['symbol'],
                            'regularMarketPrice': price,
                            'regularMarketChange': change,
                            'regularMarketChangePercent': change_pct,
                        })
                        print(f"  {asset['name']}: ${price:,.2f} ({change_pct:+.2f}%)", flush=True)
                    else:
                        print(f"  {asset['name']}: No price in response", flush=True)
                else:
                    print(f"  {asset['name']}: No data in response", flush=True)
            else:
                print(f"  {asset['name']}: HTTP {response.status_code}", flush=True)
                
        except Exception as e:
            print(f"  {asset['name']}: Error - {e}", flush=True)
    
    if results:
        return {'quoteResponse': {'result': results}}
    
    return None

def fetch_from_investing_scrape():
    """Scrape prices directly from investing.com pages"""
    try:
        from curl_cffi import requests
    except ImportError:
        return None
    
    results = []
    
    for key, asset in ASSETS.items():
        url = asset['url']
        try:
            response = requests.get(
                url,
                impersonate="chrome",
                timeout=15
            )
            
            if response.status_code != 200:
                continue
            
            html = response.text
            price = None
            change = 0
            change_pct = 0
            
            # Extract data block with last, change, and changePcr
            # Pattern: "last":106.6345,"changePcr":-7.81,"change":-9.031
            data_match = re.search(r'"last":\s*([\d.]+)[^}]*?"changePcr":\s*(-?[\d.]+)[^}]*?"change":\s*(-?[\d.]+)', html)
            if data_match:
                price = float(data_match.group(1))
                change_pct = float(data_match.group(2))
                change = float(data_match.group(3))
            else:
                # Fallback: try alternate order
                data_match = re.search(r'"last":\s*([\d.]+)[^}]*?"change":\s*(-?[\d.]+)[^}]*?"changePcr":\s*(-?[\d.]+)', html)
                if data_match:
                    price = float(data_match.group(1))
                    change = float(data_match.group(2))
                    change_pct = float(data_match.group(3))
                else:
                    # Last fallback: just get price
                    match = re.search(r'"last":\s*([\d.]+)', html)
                    if match:
                        price = float(match.group(1))
                    
                    # Try to get change separately
                    match = re.search(r'"change":\s*(-?[\d.]+)', html)
                    if match:
                        change = float(match.group(1))
                    
                    # Try changePcr
                    match = re.search(r'"changePcr":\s*(-?[\d.]+)', html)
                    if match:
                        change_pct = float(match.group(1))
            
            if price and price > 0:
                results.append({
                    'symbol': asset['symbol'],
                    'regularMarketPrice': price,
                    'regularMarketChange': change,
                    'regularMarketChangePercent': change_pct,
                })
                print(f"  {asset['name']}: ${price:,.2f} (chg: {change:+.4f}, {change_pct:+.4f}%)", flush=True)
                    
        except Exception as e:
            print(f"  {asset['name']}: Scrape error - {e}", flush=True)
    
    if results:
        return {'quoteResponse': {'result': results}}
    
    return None

def update_price_cache():
    """Update the price cache from investing.com"""
    global price_cache
    
    print("Fetching from investing.com API...", flush=True)
    
    # Try API first
    data = fetch_from_investing_api()
    
    # If API fails, try scraping
    if not data or len(data.get('quoteResponse', {}).get('result', [])) < 4:
        print("API incomplete, trying scrape...", flush=True)
        scrape_data = fetch_from_investing_scrape()
        if scrape_data:
            # Merge results
            if data:
                existing = {r['symbol']: r for r in data['quoteResponse']['result']}
                for r in scrape_data['quoteResponse']['result']:
                    if r['symbol'] not in existing:
                        existing[r['symbol']] = r
                data = {'quoteResponse': {'result': list(existing.values())}}
            else:
                data = scrape_data
    
    if data and data.get('quoteResponse', {}).get('result'):
        with price_cache['lock']:
            price_cache['data'] = data
            price_cache['last_update'] = time.time()
        print(f"Updated {len(data['quoteResponse']['result'])} prices", flush=True)

def background_price_updater():
    """Background thread that continuously updates prices"""
    while True:
        try:
            update_price_cache()
        except Exception as e:
            print(f"Update error: {e}", flush=True)
        time.sleep(2)  # Update every 2 seconds

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
