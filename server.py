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

# Investing.com configuration - Using exact URLs for live prices
ASSETS = {
    'silver': {
        'pair_id': '8836',
        'symbol': 'XAG/USD',
        'name': 'Silver',
        'url': 'https://www.investing.com/commodities/silver'
    },
    'gold': {
        'pair_id': '8830',
        'symbol': 'XAU/USD', 
        'name': 'Gold',
        'url': 'https://www.investing.com/commodities/gold'
    },
    'sp500': {
        'pair_id': '166',
        'symbol': '^GSPC',
        'name': 'S&P 500',
        'url': 'https://www.investing.com/indices/us-spx-500'
    },
    'nasdaq': {
        'pair_id': '14958',
        'symbol': '^IXIC',
        'name': 'Nasdaq',
        'url': 'https://www.investing.com/indices/nasdaq-composite'
    },
    'sp500_futures': {
        'pair_id': '1175153',
        'symbol': 'ES',
        'name': 'S&P 500 Futures',
        'url': 'https://www.investing.com/indices/us-spx-500-futures'
    },
    'nasdaq_futures': {
        'pair_id': '1175151',
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
    """Fetch live prices from investing.com - API is blocked, so use scraping"""
    # API is blocked by Cloudflare, skip and use scraping
    return None

def fetch_from_investing_scrape():
    """Scrape prices directly from investing.com pages"""
    import requests
    
    results = []
    
    # Use ScraperAPI or similar proxy service (free tier available)
    # For now, try multiple user agents and methods
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    ]
    
    # Try with curl_cffi first (better at bypassing blocks)
    try:
        from curl_cffi import requests as curl_requests
        use_curl = True
        print("Using curl_cffi", flush=True)
    except Exception as e:
        print(f"curl_cffi not available: {e}", flush=True)
        use_curl = False
    
    for key, asset in ASSETS.items():
        url = asset['url']
        price = None
        change = 0
        change_pct = 0
        
        # Try curl_cffi with different browser impersonations
        if use_curl:
            for browser in ['chrome120', 'chrome119', 'chrome110', 'safari15_5']:
                try:
                    response = curl_requests.get(
                        url,
                        impersonate=browser,
                        timeout=30,
                    )
                    
                    if response.status_code == 200:
                        html = response.text
                        if 'Just a moment' not in html and len(html) > 10000:
                            # Extract price
                            price_match = re.search(r'data-test="instrument-price-last"[^>]*>([^<]+)', html)
                            if price_match:
                                price_str = price_match.group(1).replace(',', '').strip()
                                price = float(price_str)
                            
                            if not price:
                                match = re.search(r'"last":\s*([\d.]+)', html)
                                if match:
                                    price = float(match.group(1))
                            
                            # Get change
                            change_match = re.search(r'"change":\s*(-?[\d.]+)', html)
                            if change_match:
                                change = float(change_match.group(1))
                            
                            pct_match = re.search(r'"changePercent":\s*(-?[\d.]+)', html)
                            if not pct_match:
                                pct_match = re.search(r'"changePcr":\s*(-?[\d.]+)', html)
                            if pct_match:
                                change_pct = float(pct_match.group(1))
                            
                            if price:
                                print(f"  {asset['name']}: ${price:,.4f} (via {browser})", flush=True)
                                break
                except Exception as e:
                    continue
        
        # Fallback to standard requests if curl_cffi failed
        if not price:
            for ua in user_agents:
                try:
                    response = requests.get(
                        url,
                        timeout=20,
                        headers={
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Accept-Encoding': 'gzip, deflate, br',
                            'User-Agent': ua,
                            'Cache-Control': 'no-cache',
                            'Pragma': 'no-cache',
                        }
                    )
                    
                    if response.status_code == 200:
                        html = response.text
                        if 'Just a moment' not in html and len(html) > 10000:
                            price_match = re.search(r'data-test="instrument-price-last"[^>]*>([^<]+)', html)
                            if price_match:
                                price_str = price_match.group(1).replace(',', '').strip()
                                price = float(price_str)
                            
                            if not price:
                                match = re.search(r'"last":\s*([\d.]+)', html)
                                if match:
                                    price = float(match.group(1))
                            
                            change_match = re.search(r'"change":\s*(-?[\d.]+)', html)
                            if change_match:
                                change = float(change_match.group(1))
                            
                            pct_match = re.search(r'"changePercent":\s*(-?[\d.]+)', html)
                            if not pct_match:
                                pct_match = re.search(r'"changePcr":\s*(-?[\d.]+)', html)
                            if pct_match:
                                change_pct = float(pct_match.group(1))
                            
                            if price:
                                print(f"  {asset['name']}: ${price:,.4f} (via requests)", flush=True)
                                break
                except Exception as e:
                    continue
        
        if price and price > 0:
            results.append({
                'symbol': asset['symbol'],
                'regularMarketPrice': price,
                'regularMarketChange': change,
                'regularMarketChangePercent': change_pct,
            })
        else:
            print(f"  {asset['name']}: FAILED to get price", flush=True)
    
    print(f"Total results: {len(results)}/{len(ASSETS)}", flush=True)
    
    if results:
        return {'quoteResponse': {'result': results}}
    
    return None

def update_price_cache():
    """Update the price cache from investing.com"""
    global price_cache
    
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S")
    
    # Try scraping (API is blocked by Cloudflare)
    data = fetch_from_investing_scrape()
    
    if data and data.get('quoteResponse', {}).get('result'):
        num_prices = len(data['quoteResponse']['result'])
        with price_cache['lock']:
            price_cache['data'] = data
            price_cache['last_update'] = time.time()
        print(f"[{timestamp}] Updated {num_prices} prices", flush=True)
        return True
    else:
        print(f"[{timestamp}] Failed to fetch prices, keeping last data", flush=True)
        return False

def background_price_updater():
    """Background thread that continuously updates prices 24/7"""
    consecutive_failures = 0
    max_failures = 10
    
    while True:
        try:
            success = update_price_cache()
            if success:
                consecutive_failures = 0
                time.sleep(3)  # Normal: update every 3 seconds
            else:
                consecutive_failures += 1
                # Exponential backoff on failures (max 60 seconds)
                wait_time = min(3 * (2 ** consecutive_failures), 60)
                print(f"  Retry in {wait_time}s (failure #{consecutive_failures})", flush=True)
                time.sleep(wait_time)
                
        except Exception as e:
            consecutive_failures += 1
            print(f"Update error: {e}", flush=True)
            # Wait before retrying
            wait_time = min(3 * (2 ** consecutive_failures), 60)
            time.sleep(wait_time)
            
        # Reset failure count periodically to recover from temporary issues
        if consecutive_failures >= max_failures:
            print("Too many failures, resetting and continuing...", flush=True)
            consecutive_failures = 0
            time.sleep(30)

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
