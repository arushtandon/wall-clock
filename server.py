"""
Wall Clock Server - Live prices via Interactive Brokers
Run: python server.py
Then open: http://localhost:8080

Requires: IB Gateway running and logged in
"""

from flask import Flask, jsonify, send_file, request
import json
import os
import threading
import time
from xml.etree import ElementTree
from urllib.request import urlopen, Request
from urllib.error import URLError

PORT = int(os.environ.get('PORT', 8080))
# Bump when deploying — check GET /api/status or /api/prices "wallclock.version" to confirm server runs new code.
WALLCLOCK_VERSION = os.environ.get('WALLCLOCK_VERSION', '2026-08-06.1')
app = Flask(__name__, static_folder=os.path.dirname(os.path.abspath(__file__)))

# IB subscription metadata (e.g. qualified contract string) — survives price tick merges
ib_subscribed_meta = {}

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

# YM (Dow) uses TradingView backup (IB often returns Error 354)
ym_price_thread = None
ym_last_fetch = 0

# ============== IBKR Configuration ==============
IB_HOST = '127.0.0.1'
IB_PORTS = [4001, 4002, 7496, 7497]  # Common IB Gateway ports
IB_CLIENT_ID = 1
# Market data type: 1=Live, 3=Delayed (use 3 if you get Error 354 for Dow/CBOT - no live CBOT subscription)
IB_MARKET_DATA_TYPE = int(os.environ.get('WALLCLOCK_MARKET_DATA_TYPE', '1'))
# Reconnect IBKR on this interval to re-subscribe futures after monthly roll (default: daily).
WALLCLOCK_CONTRACT_REFRESH_SEC = int(os.environ.get('WALLCLOCK_CONTRACT_REFRESH_SEC', str(24 * 3600)))
# Brent IB chain: nymex_first (default), ice_first (COIL/BRN before BZ), ice_only, nymex_only
WALLCLOCK_BRENT_CHAIN = (os.environ.get('WALLCLOCK_BRENT_CHAIN') or 'nymex_first').strip().lower()


def brent_search_order():
    """Symbol/exchange pairs for Brent — order depends on WALLCLOCK_BRENT_CHAIN."""
    nymex = [('BZ', 'NYMEX'), ('BZ', 'IPE')]
    ice = [('COIL', 'IPE'), ('BRN', 'IPE')]
    m = WALLCLOCK_BRENT_CHAIN
    if m == 'ice_first':
        return ice + nymex
    if m == 'ice_only':
        return ice
    if m == 'nymex_only':
        return nymex
    return nymex + ice  # nymex_first

# ============== Re-auth notification: once per week (168h). Set NOTIFY_THROTTLE_HOURS=168 on server. ==============
NOTIFY_THROTTLE_HOURS = float(os.environ.get('NOTIFY_THROTTLE_HOURS', '168'))  # Weekly = 168; daily = 24. App sends re-auth email at most this often.
NOTIFY_THROTTLE_SEC = int(NOTIFY_THROTTLE_HOURS * 3600)
FAILURES_BEFORE_NOTIFY = 3       # After this many connection failures, send notification
_last_reauth_notification_time = 0
_consecutive_failures = 0

# Optional: set in environment to get automatic alerts when login is required
NOTIFY_EMAIL = os.environ.get('NOTIFY_EMAIL', 'arush.tandon@safronltd.com')  # Default notification email
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
NOTIFY_NOVNC_URL = os.environ.get('NOTIFY_NOVNC_URL', 'https://safronliveprices.duckdns.org/novnc/vnc.html')
TWELVEDATA_API_KEY = os.environ.get('TWELVEDATA_API_KEY', '31a5dfc7fc7a4f8cabe8ebf2e707b82a')
_twelvedata_block_until = 0

# Market news: Google News, Reuters, CNBC TV18, Investing.com, Bloomberg, etc.
NEWS_FEEDS = [
    # Google News – top stories and business (breaking news, latest first)
    'https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en',
    'https://news.google.com/rss/search?q=stock+market+OR+markets+OR+breaking+news&hl=en-US&gl=US&ceid=US:en',
    # Reuters (via feed)
    'https://cdn.feedcontrol.net/8/1114-wioSIX3uu8MEi.xml',
    'https://cdn.feedcontrol.net/8/1115-TvWAhu4G064WT.xml',
    # CNBC TV18 (India – markets, business, economy)
    'https://www.cnbctv18.com/commonfeeds/v1/cne/rss/latest.xml',
    'https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml',
    'https://www.cnbctv18.com/commonfeeds/v1/cne/rss/business.xml',
    'https://www.cnbctv18.com/commonfeeds/v1/cne/rss/economy.xml',
    # Investing.com
    'https://www.investing.com/rss/news_287.rss',
    'https://www.investing.com/rss/news_25.rss',
    'https://www.investing.com/rss/news_11.rss',
    'https://www.investing.com/rss/news_1.rss',
    # Bloomberg
    'https://feeds.bloomberg.com/markets/news.rss',
    'https://feeds.bloomberg.com/business/news.rss',
    'https://feeds.bloomberg.com/economics/news.rss',
    # Fallbacks
    'https://finance.yahoo.com/news/rssindex',
    'https://feeds.bbci.co.uk/news/business/rss.xml',
    'https://feeds.npr.org/1001/rss.xml',
]
NEWS_USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
NEWS_HEADERS = {
    'User-Agent': NEWS_USER_AGENT,
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}
NEWS_CACHE = {'articles': [], 'updated': 0}
NEWS_CACHE_TTL = 60  # 1 minute – refresh every minute so breaking news shows quickly
NEWS_MAX_STORED = 100
NEWS_MAX_AGE_HOURS = 24  # Only articles from the last 24 hours
# Keywords for assets and breaking news (used as tie-breaker when same time)
NEWS_ASSET_KEYWORDS = (
    'breaking', 'breaking news',
    'gold', 'silver', 'oil', 'crude', 'wti', 'brent', 'opec',
    'bitcoin', 'btc', 'crypto', 'cryptocurrency',
    's&p 500', 'sp500', 'nasdaq', 'dow', 'stock market', 'stocks', 'futures',
    'nifty', 'sensex', 'india', 'bse', 'nse',
    'hang seng', 'hsi', 'hong kong', 'china',
    'kospi', 'korea', 'kospi 200',
    'fed', 'fomc', 'interest rate', 'inflation', 'cpi', 'jobs report',
    'earnings', 'recession', 'gdp', 'treasury', 'dollar', 'dxy',
    'russell', 'rty', 'nikkei', 'dax', 'copper', 'taiwan', 'mtw',
)
NEWS_LOCK = threading.Lock()

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
    },
    'ym_futures': {
        'name': 'Dow Futures',
        'display_symbol': 'YM',
    },
    'hsi_futures': {
        'name': 'Hang Seng',
        'display_symbol': 'HSI',
    },
    'btc_futures': {
        'name': 'Bitcoin',
        'display_symbol': 'BTC',
    },
    'crude_futures': {
        'name': 'Brent Futures',
        'display_symbol': 'BZ',
    },
    'kospi_200': {
        'name': 'Kospi 200',
        'display_symbol': 'KOSPI200',
    },
    'dollar_index': {
        'name': 'Dollar Index',
        'display_symbol': 'DXY',
    },
    'eur_usd': {
        'name': 'EUR/USD',
        'display_symbol': 'EURUSD',
    },
    'usd_jpy': {
        'name': 'USD/JPY',
        'display_symbol': 'USDJPY',
    },
    'usd_hkd': {
        'name': 'USD/HKD',
        'display_symbol': 'USDHKD',
    },
    'gbp_usd': {
        'name': 'GBP/USD',
        'display_symbol': 'GBPUSD',
    },
    'usd_inr': {
        'name': 'USD/INR',
        'display_symbol': 'USDINR',
    },
    'mtw_futures': {
        'name': 'MTW (HKFE)',
        'display_symbol': 'MTW',
    },
    'rty_futures': {
        'name': 'Russell 2000',
        'display_symbol': 'RTY',
    },
    'nikkei_futures': {
        'name': 'Nikkei (CME)',
        'display_symbol': 'NKD',
    },
    'dax_futures': {
        'name': 'DAX',
        'display_symbol': 'DAX',
    },
    'wti_futures': {
        'name': 'WTI Crude',
        'display_symbol': 'CL',
    },
    'copper_futures': {
        'name': 'Copper',
        'display_symbol': 'HG',
    },
}

# Demo/sample prices when IB Gateway is not connected (so localhost still shows numbers)
DEMO_PRICES = [
    {'symbol': 'XAG/USD', 'regularMarketPrice': 24.52, 'regularMarketChange': 0.11, 'regularMarketChangePercent': 0.45},
    {'symbol': 'XAU/USD', 'regularMarketPrice': 2034.80, 'regularMarketChange': 12.50, 'regularMarketChangePercent': 0.62},
    {'symbol': 'ES', 'regularMarketPrice': 5125.25, 'regularMarketChange': -8.75, 'regularMarketChangePercent': -0.17},
    {'symbol': 'NQ', 'regularMarketPrice': 18102.50, 'regularMarketChange': 42.25, 'regularMarketChangePercent': 0.23},
    {'symbol': 'NIFTY', 'regularMarketPrice': 24250.00, 'regularMarketChange': 85.00, 'regularMarketChangePercent': 0.35},
    {'symbol': 'YM', 'regularMarketPrice': 39520.00, 'regularMarketChange': -120.00, 'regularMarketChangePercent': -0.30},
    {'symbol': 'HSI', 'regularMarketPrice': 16180.00, 'regularMarketChange': 95.00, 'regularMarketChangePercent': 0.59},
    {'symbol': 'BTC', 'regularMarketPrice': 43250.00, 'regularMarketChange': 520.00, 'regularMarketChangePercent': 1.22},
    {'symbol': 'BZ', 'regularMarketPrice': 78.50, 'regularMarketChange': 0.85, 'regularMarketChangePercent': 1.09},
    {'symbol': 'KOSPI200', 'regularMarketPrice': 385.00, 'regularMarketChange': 2.50, 'regularMarketChangePercent': 0.65},
    {'symbol': 'DXY', 'regularMarketPrice': 104.25, 'regularMarketChange': 0.12, 'regularMarketChangePercent': 0.12},
    {'symbol': 'EURUSD', 'regularMarketPrice': 1.0825, 'regularMarketChange': -0.0020, 'regularMarketChangePercent': -0.18},
    {'symbol': 'USDJPY', 'regularMarketPrice': 151.20, 'regularMarketChange': 0.45, 'regularMarketChangePercent': 0.30},
    {'symbol': 'USDHKD', 'regularMarketPrice': 7.8200, 'regularMarketChange': 0.0020, 'regularMarketChangePercent': 0.03},
    {'symbol': 'GBPUSD', 'regularMarketPrice': 1.2640, 'regularMarketChange': -0.0030, 'regularMarketChangePercent': -0.24},
    {'symbol': 'USDINR', 'regularMarketPrice': 83.24, 'regularMarketChange': 0.12, 'regularMarketChangePercent': 0.14},
    {'symbol': 'MTW', 'regularMarketPrice': 920.00, 'regularMarketChange': 4.50, 'regularMarketChangePercent': 0.49},
    {'symbol': 'RTY', 'regularMarketPrice': 2150.50, 'regularMarketChange': -8.20, 'regularMarketChangePercent': -0.38},
    {'symbol': 'NKD', 'regularMarketPrice': 38500.00, 'regularMarketChange': 120.00, 'regularMarketChangePercent': 0.31},
    {'symbol': 'DAX', 'regularMarketPrice': 18450.00, 'regularMarketChange': 55.00, 'regularMarketChangePercent': 0.30},
    {'symbol': 'CL', 'regularMarketPrice': 78.20, 'regularMarketChange': 0.65, 'regularMarketChangePercent': 0.84},
    {'symbol': 'HG', 'regularMarketPrice': 4.52, 'regularMarketChange': 0.03, 'regularMarketChangePercent': 0.67},
]
def get_front_month():
    """Get the front month contract date (YYYYMM) for quarterly futures (ES, NQ)."""
    from datetime import datetime
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


def get_gc_contract_month():
    """GC (Gold) current continuous contract: Apr, Jun, Aug, Oct, Dec. April in Jan–Apr, then auto-roll."""
    from datetime import datetime
    now = datetime.now()
    y, m = now.year, now.month
    months = [4, 6, 8, 10, 12]  # continuous cycle
    for mo in months:
        if mo >= m:
            return f"{y}{mo:02d}"
    return f"{y}04"  # Jan–Mar: current year April


def get_hg_contract_month():
    """HG (Copper) COMEX months: Mar, May, Jul, Sep, Dec."""
    from datetime import datetime
    now = datetime.now()
    y, m = now.year, now.month
    months = [3, 5, 7, 9, 12]
    for mo in months:
        if mo >= m:
            return f"{y}{mo:02d}"
    return f"{y + 1}03"


def get_si_contract_month():
    """SI (Silver) contract months: Mar, May, Sep, Dec. Auto-roll to next when current expires."""
    from datetime import datetime
    now = datetime.now()
    y, m = now.year, now.month
    # COMEX SI active cycle includes Jul as well.
    months = [3, 5, 7, 9, 12]
    for mo in months:
        if mo >= m:
            return f"{y}{mo:02d}"
    return f"{y+1}{months[0]:02d}"


def get_nifty_front_month():
    """Get front month for Nifty (monthly expiry, last Thursday of month). Returns YYYYMM."""
    from datetime import datetime
    now = datetime.now()
    year, month = now.year, now.month
    # Current month is front until last Thursday has passed
    return f"{year}{month:02d}"


def get_cl_front_month():
    """Calendar heuristic for Brent month (YYYYMM): next calendar month.
    Used only when IB chain data is unavailable. Live/streaming uses pick_brent_front_contract()."""
    from datetime import datetime
    now = datetime.now()
    year, month = now.year, now.month
    if month == 12:
        return f"{year + 1}01"
    return f"{year}{month + 1:02d}"


def _contract_details_expiry_date(cd):
    """Best-effort expiry/last-trade date for a futures ContractDetails (IBKR)."""
    from datetime import datetime, date
    import calendar
    s = getattr(cd, 'realExpirationDate', None) or ''
    s = str(s).strip()
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return datetime.strptime(s[:8], '%Y%m%d').date()
        except ValueError:
            pass
    raw = (getattr(cd.contract, 'lastTradeDateOrContractMonth', None) or '').strip()
    if len(raw) >= 6 and raw[:6].isdigit():
        y, m = int(raw[:4]), int(raw[4:6])
        last = calendar.monthrange(y, m)[1]
        return date(y, m, last)
    return None


def pick_brent_front_contract(ib):
    """
    Choose the actively traded front Brent contract using IBKR's contract chain.
    Picks the listed contract with the nearest real expiration date >= today (same as exchange 'M1' / front month).
    Search order: BZ@NYMEX, BZ@IPE, COIL@ICE, BRN@IPE — first chain that qualifies wins.
    Falls back to resolve_brent_contract(ib, get_cl_front_month()) if expiry data is missing.
    """
    from datetime import date
    from ib_insync import Contract
    today = date.today()
    searches = [
        ('BZ', 'NYMEX'),
        ('BZ', 'IPE'),
        ('COIL', 'IPE'),
        ('BRN', 'IPE'),
    ]
    for symbol, exchange in searches:
        try:
            s = Contract()
            s.symbol = symbol
            s.secType = 'FUT'
            s.exchange = exchange
            s.currency = 'USD'
            matches = ib.reqContractDetails(s)
            if not matches:
                continue
            scored = []
            for cd in matches:
                exp = _contract_details_expiry_date(cd)
                if exp is not None:
                    scored.append((exp, cd))
            if not scored:
                continue
            scored.sort(key=lambda x: x[0])
            active = [(e, cd) for e, cd in scored if e >= today]
            pool = active if active else scored
            exp_chosen, chosen_cd = pool[0]
            q = ib.qualifyContracts(chosen_cd.contract)
            if q:
                c = q[0]
                print(
                    f"Brent front contract (nearest expiry {exp_chosen} >= {today}): {symbol}@{exchange} -> {c}",
                    flush=True,
                )
                return c
        except Exception as e:
            print(f"Brent front pick failed {symbol}@{exchange}: {e}", flush=True)
    print("Brent front pick: falling back to calendar month resolver", flush=True)
    return resolve_brent_contract(ib, get_cl_front_month())


def pick_silver_front_contract(ib):
    """
    Choose front SI contract from IB chain using nearest expiry >= today.
    Falls back to calendar heuristic month if chain data is unavailable.
    """
    from datetime import date
    from ib_insync import Contract, Future
    today = date.today()
    try:
        s = Contract()
        s.symbol = 'SI'
        s.secType = 'FUT'
        s.exchange = 'COMEX'
        s.currency = 'USD'
        matches = ib.reqContractDetails(s)
        if matches:
            scored = []
            for cd in matches:
                c = cd.contract
                # Keep only standard SI futures (5000 oz). IB can return micro/other variants
                # that do not match the intended wall-clock silver contract.
                mult = str(getattr(c, 'multiplier', '') or '')
                tclass = str(getattr(c, 'tradingClass', '') or '').upper()
                lsymbol = str(getattr(c, 'localSymbol', '') or '').upper()
                if mult != '5000':
                    continue
                if tclass and tclass != 'SI':
                    continue
                if lsymbol and not lsymbol.startswith('SI'):
                    continue
                exp = _contract_details_expiry_date(cd)
                if exp is not None:
                    scored.append((exp, cd))
            if scored:
                scored.sort(key=lambda x: x[0])
                active = [(e, cd) for e, cd in scored if e >= today]
                pool = active if active else scored
                exp_chosen, chosen_cd = pool[0]
                q = ib.qualifyContracts(chosen_cd.contract)
                if q:
                    c = q[0]
                    print(
                        f"Silver front contract (nearest expiry {exp_chosen} >= {today}): {c}",
                        flush=True,
                    )
                    return c
    except Exception as e:
        print(f"Silver front pick failed: {e}", flush=True)
    # Fallback 1: explicit month heuristic.
    si_month = get_si_contract_month()
    try:
        cand = Future('SI', si_month, 'COMEX')
        q = ib.qualifyContracts(cand)
        if q:
            print(f"Silver fallback qualified by month {si_month}: {q[0]}", flush=True)
            return q[0]
    except Exception as e:
        print(f"Silver fallback month {si_month} failed: {e}", flush=True)

    # Fallback 2: probe nearby listed SI months (includes July cycle).
    from datetime import datetime
    now = datetime.now()
    probe_months = [3, 5, 7, 9, 12]
    for year in [now.year, now.year + 1]:
        for mo in probe_months:
            ym = f"{year}{mo:02d}"
            try:
                cand = Future('SI', ym, 'COMEX')
                q = ib.qualifyContracts(cand)
                if q:
                    print(f"Silver fallback probe qualified {ym}: {q[0]}", flush=True)
                    return q[0]
            except Exception:
                pass

    # Fallback 3: micro silver if SI cannot be qualified on this account.
    try:
        ms = Future('SIL', get_front_month(), 'COMEX')
        q = ib.qualifyContracts(ms)
        if q:
            print(f"Silver fallback using micro SIL: {q[0]}", flush=True)
            return q[0]
    except Exception as e:
        print(f"Silver micro fallback failed: {e}", flush=True)

    print("Silver front pick failed: no contract could be qualified", flush=True)
    return None


def get_brent_candidates(month):
    """Return Brent futures contract candidates for IBKR qualification (order follows WALLCLOCK_BRENT_CHAIN)."""
    from ib_insync import Future
    return [Future(sym, month, ex) for sym, ex in brent_search_order()]


def _norm_contract_month(raw):
    """Normalize IB contract month formats to YYYYMM for sorting."""
    raw = (raw or '').strip().replace(' ', '')
    if len(raw) >= 6 and raw[:6].isdigit():
        return raw[:6]
    if raw.isdigit():
        return raw[:6]
    months = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
    for k, v in months.items():
        if raw.upper().startswith(k):
            yr = raw[len(k):].strip()
            if len(yr) == 2:
                yr = '20' + yr
            if len(yr) == 4 and yr.isdigit():
                return f"{yr}{v:02d}"
    return '999999'


def resolve_brent_contract(ib, target_month):
    """Find the best Brent contract IBKR can qualify for this account."""
    from ib_insync import Contract
    for symbol, exchange in brent_search_order():
        try:
            s = Contract()
            s.symbol = symbol
            s.secType = 'FUT'
            s.exchange = exchange
            s.currency = 'USD'
            matches = ib.reqContractDetails(s)
            if not matches:
                continue
            matches_sorted = sorted(matches, key=lambda m: _norm_contract_month(getattr(m.contract, 'lastTradeDateOrContractMonth', '')))
            # Prefer target month, else nearest later month, else earliest available.
            exact = [m for m in matches_sorted if _norm_contract_month(getattr(m.contract, 'lastTradeDateOrContractMonth', '')) == target_month]
            if exact:
                c = exact[0].contract
                print(f"Brent exact month qualified by search {symbol}@{exchange}: {c}", flush=True)
                return c
            later = [m for m in matches_sorted if _norm_contract_month(getattr(m.contract, 'lastTradeDateOrContractMonth', '')) > target_month]
            if later:
                c = later[0].contract
                print(f"Brent next month qualified by search {symbol}@{exchange}: {c}", flush=True)
                return c
            c = matches_sorted[0].contract
            print(f"Brent fallback qualified by search {symbol}@{exchange}: {c}", flush=True)
            return c
        except Exception as e:
            print(f"Brent search failed {symbol}@{exchange}: {e}", flush=True)
    return None


def send_reauth_notification():
    """Send one notification that IBKR re-auth is required (throttled to once per NOTIFY_THROTTLE_SEC)."""
    global _last_reauth_notification_time
    now = time.time()
    if now - _last_reauth_notification_time < NOTIFY_THROTTLE_SEC:
        return
    _last_reauth_notification_time = now
    msg = (
        "IBKR connection lost. "
        "Wall Clock is now using backup prices from TradingView. "
        "To restore IB prices, log in via noVNC: %s\n\n"
        "Backup prices are active and updating automatically."
    ) % NOTIFY_NOVNC_URL
    # Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            import urllib.request
            import urllib.parse
            url = "https://api.telegram.org/bot%s/sendMessage?chat_id=%s&text=%s" % (
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, urllib.parse.quote(msg)
            )
            urllib.request.urlopen(url, timeout=10)
            print("Sent Telegram re-auth notification", flush=True)
        except Exception as e:
            print("Telegram notify failed: %s" % e, flush=True)
    # Email - Always send if email is configured
    if NOTIFY_EMAIL:
        try:
            import smtplib
            from email.mime.text import MIMEText
            m = MIMEText(msg)
            m['Subject'] = "IBKR Connection Lost - Wall Clock Using Backup Prices"
            m['From'] = SMTP_USER if SMTP_USER else 'wallclock@safronltd.com'
            m['To'] = NOTIFY_EMAIL
            if SMTP_USER and SMTP_PASS:
                # Use configured SMTP credentials
                print(f"Attempting to send email to {NOTIFY_EMAIL} via {SMTP_HOST}:{SMTP_PORT}...", flush=True)
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                    s.starttls()
                    s.login(SMTP_USER, SMTP_PASS)
                    s.sendmail(SMTP_USER, NOTIFY_EMAIL, m.as_string())
                print(f"✅ Sent email notification to {NOTIFY_EMAIL}", flush=True)
            else:
                print(f"⚠️ SMTP credentials not configured (SMTP_USER: {bool(SMTP_USER)}, SMTP_PASS: {bool(SMTP_PASS)})", flush=True)
                # Try to send via system mail (if configured)
                try:
                    import subprocess
                    proc = subprocess.Popen(['mail', '-s', m['Subject'], NOTIFY_EMAIL], 
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    proc.communicate(input=msg.encode())
                    print(f"Sent email notification to {NOTIFY_EMAIL} via system mail", flush=True)
                except Exception as mail_err:
                    print(f"System mail also failed: {mail_err}", flush=True)
        except Exception as e:
            print(f"❌ Email notify failed: {e}", flush=True)
            import traceback
            traceback.print_exc()

def send_test_notification():
    """Send one test email (same as disconnect alert) without affecting throttle. Returns (success, message)."""
    if not NOTIFY_EMAIL:
        return False, "NOTIFY_EMAIL not set"
    if not SMTP_USER or not SMTP_PASS:
        return False, "SMTP_USER or SMTP_PASS not set"
    msg = (
        "This is a TEST notification from Wall Clock.\n\n"
        "When IBKR disconnects, you will receive an email like this (at most once per week).\n\n"
        "noVNC link: %s\n\n"
        "If you received this, disconnect email is working."
    ) % NOTIFY_NOVNC_URL
    try:
        import smtplib
        from email.mime.text import MIMEText
        m = MIMEText(msg)
        m['Subject'] = "Test: IBKR disconnect notification"
        m['From'] = SMTP_USER
        m['To'] = NOTIFY_EMAIL
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, NOTIFY_EMAIL, m.as_string())
        print(f"Test email sent to {NOTIFY_EMAIL}", flush=True)
        return True, f"Sent to {NOTIFY_EMAIL}"
    except Exception as e:
        print(f"Test email failed: {e}", flush=True)
        return False, str(e)

# Yahoo symbols are kept for chart fallback and as metadata references.
YAHOO_SYMBOLS = {
    'gold': 'GC=F',           # Gold futures
    'silver': 'SI=F',         # Silver futures
    'sp500_futures': 'ES=F',  # S&P 500 E-mini futures
    'nasdaq_futures': 'NQ=F', # Nasdaq E-mini futures
    'ym_futures': 'YM=F',     # Dow E-mini futures
    'hsi_futures': 'HSI=F',   # Hang Seng Index futures
    'btc_futures': 'BTC=F',   # Bitcoin futures
    'nifty_futures': '^NSEI',  # Nifty 50 index (fallback for chart)
    'crude_futures': 'BZ=F',    # Brent crude oil futures
    'kospi_200': '^KS200',      # Kospi 200 index (Korea)
    'dollar_index': 'DX-Y.NYB', # US Dollar Index
    'eur_usd': 'EURUSD=X',
    'usd_jpy': 'JPY=X',
    'usd_hkd': 'HKD=X',
    'gbp_usd': 'GBPUSD=X',
    'usd_inr': 'INR=X',         # USD/INR FX spot
    'mtw_futures': 'TW=F',       # rough Yahoo fallback (prefer TradingView)
    'rty_futures': 'RTY=F',      # Russell 2000 E-mini
    'nikkei_futures': 'NKD=F',   # Nikkei USD futures (CME)
    'dax_futures': '^GDAXI',     # DAX index fallback
    'wti_futures': 'CL=F',       # WTI crude
    'copper_futures': 'HG=F',    # Copper futures
}
# Backup symbols from TradingView scanner (non-Yahoo fallback source).
# We try symbols in order until one returns valid data.
BACKUP_TV_SYMBOLS = {
    'gold': ['COMEX:GC1!', 'TVC:GOLD'],
    'silver': ['COMEX:SI1!', 'TVC:SILVER'],
    'sp500_futures': ['CME_MINI:ES1!', 'CME:ES1!'],
    'nasdaq_futures': ['CME_MINI:NQ1!', 'CME:NQ1!'],
    'ym_futures': ['CBOT_MINI:YM1!', 'CBOT:YM1!'],
    'hsi_futures': ['HKEX:HSI1!', 'HSI:HSI'],
    'btc_futures': ['CME:BTC1!', 'BINANCE:BTCUSDT'],
    'nifty_futures': ['NSE:NIFTY', 'NSE:NIFTY1!'],
    # Default order overridden by get_crude_backup_tv_symbols() (see WALLCLOCK_CRUDE_BACKUP_ORDER).
    # Keep futures symbols first; UKOIL is optional via env because it can diverge from futures.
    'crude_futures': ['NYMEX:BZ1!', 'ICEEUR:BRN1!'],
    'kospi_200': ['KRX:KOSPI200', 'KRX:KOSPI200FUT'],
    'dollar_index': ['TVC:DXY', 'ICEUS:DX1!'],
    'eur_usd': ['FX:EURUSD', 'OANDA:EURUSD'],
    'usd_jpy': ['FX:USDJPY', 'OANDA:USDJPY'],
    'usd_hkd': ['FX:USDHKD', 'OANDA:USDHKD'],
    'gbp_usd': ['FX:GBPUSD', 'OANDA:GBPUSD'],
    'usd_inr': ['FX_IDC:USDINR', 'OANDA:USDINR'],
    'mtw_futures': ['HKEX:MTW1!', 'HKEX:MTW'],
    'rty_futures': ['CME_MINI:RTY1!', 'CME:RTY1!'],
    'nikkei_futures': ['CME:NKD1!', 'CME_MINI:NKD1!'],
    'dax_futures': ['EUREX:FDAX1!', 'XETR:DAX'],
    'wti_futures': ['NYMEX:CL1!', 'TVC:USOIL'],
    'copper_futures': ['COMEX:HG1!', 'TVC:COPPER'],
}


def get_silver_backup_tv_symbols():
    """
    Silver backup symbols.
    WALLCLOCK_SILVER_BACKUP_TV=comma list overrides entirely.
    WALLCLOCK_SILVER_BACKUP_ORDER=spot_first uses TVC:SILVER before COMEX:SI1!.
    """
    custom = (os.environ.get('WALLCLOCK_SILVER_BACKUP_TV') or '').strip()
    if custom:
        return [x.strip() for x in custom.split(',') if x.strip()]
    order = (os.environ.get('WALLCLOCK_SILVER_BACKUP_ORDER') or 'futures_first').strip().lower()
    if order == 'spot_first':
        return ['TVC:SILVER', 'COMEX:SI1!']
    return list(BACKUP_TV_SYMBOLS['silver'])


def get_crude_backup_tv_symbols():
    """
    TradingView symbols for Brent when IBKR does not stream a price.
    WALLCLOCK_CRUDE_BACKUP_TV=comma list overrides entirely.
    WALLCLOCK_CRUDE_BACKUP_ORDER=ice_first puts ICEEUR:BRN1! before NYMEX:BZ1!.
    """
    custom = (os.environ.get('WALLCLOCK_CRUDE_BACKUP_TV') or '').strip()
    if custom:
        return [x.strip() for x in custom.split(',') if x.strip()]
    order = (os.environ.get('WALLCLOCK_CRUDE_BACKUP_ORDER') or 'nymex_first').strip().lower()
    include_ukoil = (os.environ.get('WALLCLOCK_CRUDE_INCLUDE_UKOIL') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    if order == 'ice_first':
        symbols = ['ICEEUR:BRN1!', 'NYMEX:BZ1!']
    else:
        symbols = ['NYMEX:BZ1!', 'ICEEUR:BRN1!']
    if include_ukoil:
        symbols.append('TVC:UKOIL')
    return symbols
# Fallback Yahoo symbols when primary returns no data (e.g. HSI index vs futures)
YAHOO_FALLBACK = {'hsi_futures': '^HSI'}
# Yahoo symbol for chart fallback (when IBKR historical not available or for YM).
CHART_SYMBOLS = dict(YAHOO_SYMBOLS)
CHART_FALLBACK = {'hsi_futures': '^HSI'}
if 'nifty_futures' not in CHART_SYMBOLS:
    CHART_SYMBOLS['nifty_futures'] = '^NSEI'

# Assets that have IBKR contracts for historical chart (YM uses Yahoo only).
CHART_IBKR_ASSETS = {'gold', 'silver', 'sp500_futures', 'nasdaq_futures', 'nifty_futures', 'hsi_futures', 'btc_futures', 'crude_futures', 'kospi_200', 'usd_inr', 'eur_usd', 'usd_jpy', 'usd_hkd', 'gbp_usd', 'mtw_futures', 'rty_futures', 'nikkei_futures', 'dax_futures', 'wti_futures', 'copper_futures'}

def build_chart_contract(asset_key):
    """Build IB Contract for historical chart. Returns None for ym_futures (use Yahoo)."""
    from ib_insync import Contract, Future, Forex
    if asset_key == 'ym_futures':
        return None
    front_month = get_front_month()
    gc_month = get_gc_contract_month()
    si_month = get_si_contract_month()
    nifty_month = get_nifty_front_month()
    if asset_key == 'gold':
        c = Contract()
        c.symbol = 'GC'
        c.secType = 'FUT'
        c.exchange = 'COMEX'
        c.currency = 'USD'
        c.lastTradeDateOrContractMonth = gc_month
        c.multiplier = '100'
        return c
    if asset_key == 'silver':
        c = Contract()
        c.symbol = 'SI'
        c.secType = 'FUT'
        c.exchange = 'COMEX'
        c.currency = 'USD'
        c.lastTradeDateOrContractMonth = si_month
        c.multiplier = '5000'
        return c
    if asset_key == 'sp500_futures':
        return Future('ES', front_month, 'CME')
    if asset_key == 'nasdaq_futures':
        return Future('NQ', front_month, 'CME')
    if asset_key == 'nifty_futures':
        c = Contract()
        c.symbol = 'NIFTY'
        c.secType = 'FUT'
        c.exchange = 'SGX'
        c.currency = 'USD'
        c.lastTradeDateOrContractMonth = nifty_month
        return c
    if asset_key == 'hsi_futures':
        return Future('HSI', front_month, 'HKFE')
    if asset_key == 'btc_futures':
        return Future('BRR', front_month, 'CME')
    if asset_key == 'crude_futures':
        cl_month = get_cl_front_month()
        # Preferred chart contract; live stream uses runtime qualification across candidates.
        return Future('BZ', cl_month, 'NYMEX')
    if asset_key == 'kospi_200':
        # K2I = KOSPI 200 futures on KRX; quarterly (Mar, Jun, Sep, Dec) = same as get_front_month()
        return Future('K2I', front_month, 'KRX')
    if asset_key == 'mtw_futures':
        return Future('MTW', get_nifty_front_month(), 'HKFE')
    if asset_key == 'rty_futures':
        return Future('RTY', front_month, 'CME')
    if asset_key == 'nikkei_futures':
        return Future('NKD', front_month, 'CME')
    if asset_key == 'dax_futures':
        return Future('DAX', front_month, 'EUREX')
    if asset_key == 'wti_futures':
        return Future('CL', get_cl_front_month(), 'NYMEX')
    if asset_key == 'copper_futures':
        return Future('HG', get_hg_contract_month(), 'COMEX')
    if asset_key == 'usd_inr':
        return Forex('USDINR')
    if asset_key == 'eur_usd':
        return Forex('EURUSD')
    if asset_key == 'usd_jpy':
        return Forex('USDJPY')
    if asset_key == 'usd_hkd':
        return Forex('USDHKD')
    if asset_key == 'gbp_usd':
        return Forex('GBPUSD')
    return None

def fetch_chart_from_ibkr(asset_key):
    # Fetch 15-min OHLC for last 6 months from IBKR. Returns list of dict with time, open, high, low, close.
    import socket
    connected_port = None
    for port in IB_PORTS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            if sock.connect_ex((IB_HOST, port)) == 0:
                connected_port = port
                sock.close()
                break
            sock.close()
        except Exception:
            pass
    if not connected_port:
        return None
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect(IB_HOST, connected_port, clientId=IB_CLIENT_ID + 1, timeout=12)
        if asset_key == 'crude_futures':
            # Same front-contract logic as live stream (expiry-based chain pick).
            contract = pick_brent_front_contract(ib)
            if contract is None:
                ib.disconnect()
                return None
        else:
            contract = build_chart_contract(asset_key)
            if contract is None:
                ib.disconnect()
                return None
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            ib.disconnect()
            return None
        # 15m bars can fail for some futures/contracts depending on permissions/session.
        # Try a few request modes before giving up.
        req_attempts = [
            ('180 D', '15 mins', 'TRADES', 0),
            ('180 D', '15 mins', 'MIDPOINT', 0),
            ('180 D', '15 mins', 'TRADES', 1),
            ('90 D', '15 mins', 'TRADES', 0),
            ('90 D', '15 mins', 'MIDPOINT', 0),
            ('180 D', '1 hour', 'TRADES', 0),
        ]
        bars = None
        for duration_str, bar_size, what, use_rth in req_attempts:
            try:
                bars = ib.reqHistoricalData(
                    qualified[0],
                    endDateTime='',
                    durationStr=duration_str,
                    barSizeSetting=bar_size,
                    whatToShow=what,
                    useRTH=use_rth,
                    formatDate=1,
                )
                if bars:
                    print(
                        f"IBKR chart {asset_key}: got {len(bars)} bars "
                        f"(duration={duration_str}, size={bar_size}, what={what}, useRTH={use_rth})",
                        flush=True
                    )
                    break
                else:
                    print(
                        f"IBKR chart {asset_key}: no bars "
                        f"(duration={duration_str}, size={bar_size}, what={what}, useRTH={use_rth})",
                        flush=True
                    )
            except Exception as req_err:
                print(
                    f"IBKR chart {asset_key}: request failed "
                    f"(duration={duration_str}, size={bar_size}, what={what}, useRTH={use_rth}) -> {req_err}",
                    flush=True
                )
        ib.disconnect()
        if not bars:
            return None
        candles = []
        for b in bars:
            t = b.date if hasattr(b.date, 'timestamp') else b.date
            if hasattr(t, 'timestamp'):
                ts = int(t.timestamp())
            else:
                ts = int(t)
            candles.append({
                'time': ts,
                'open': round(float(b.open), 2),
                'high': round(float(b.high), 2),
                'low': round(float(b.low), 2),
                'close': round(float(b.close), 2),
            })
        return candles
    except Exception as e:
        print(f"IBKR chart fetch for {asset_key}: {e}", flush=True)
        return None

def fetch_chart_from_yahoo(asset_key):
    # Fallback: 15-min OHLC from Yahoo; tries multiple symbols (e.g. ^HSI for HSI) and intervals.
    symbols_to_try = []
    if asset_key in CHART_SYMBOLS:
        symbols_to_try.append(CHART_SYMBOLS[asset_key])
    if asset_key in CHART_FALLBACK:
        symbols_to_try.append(CHART_FALLBACK[asset_key])
    if not symbols_to_try:
        return None
    import requests
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://finance.yahoo.com/',
    }
    for yahoo_symbol in symbols_to_try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
        for interval, range_ in [('15m', '60d'), ('15m', '6mo'), ('1h', '6mo'), ('1d', '6mo')]:
            try:
                r = requests.get(url, params={'interval': interval, 'range': range_}, headers=headers, timeout=(10, 25))
                if r.status_code != 200:
                    continue
                data = r.json()
                if 'chart' not in data or 'result' not in data['chart'] or not data['chart']['result']:
                    continue
                result = data['chart']['result'][0]
                timestamps = result.get('timestamp', [])
                quote = result.get('indicators', {}).get('quote', [{}])
                if not quote:
                    continue
                quote = quote[0]
                o = quote.get('open', []) or []
                h = quote.get('high', []) or []
                l_ = quote.get('low', []) or []
                c = quote.get('close', []) or []
                candles = []
                for i in range(len(timestamps)):
                    if i < len(c) and c[i] is not None and c[i] > 0:
                        candles.append({
                            'time': timestamps[i],
                            'open': round(float(o[i] if i < len(o) and o[i] is not None else c[i]), 2),
                            'high': round(float(h[i] if i < len(h) and h[i] is not None else c[i]), 2),
                            'low': round(float(l_[i] if i < len(l_) and l_[i] is not None else c[i]), 2),
                            'close': round(float(c[i]), 2),
                        })
                if candles:
                    return candles
            except Exception as e:
                print(f"Yahoo chart fetch {asset_key} ({interval}/{range_}): {e}", flush=True)
                continue
    return None


def fetch_chart_from_twelvedata(asset_key):
    """Fallback OHLC from TwelveData (non-Yahoo)."""
    global _twelvedata_block_until
    if not TWELVEDATA_API_KEY:
        return None
    # If we recently hit TwelveData rate limit, skip until window resets.
    if time.time() < _twelvedata_block_until:
        wait_left = int(_twelvedata_block_until - time.time())
        print(f"TwelveData chart {asset_key}: temporarily blocked for {wait_left}s due to rate limit", flush=True)
        return None
    # Brent-focused fallback symbols first.
    symbol_map = {
        # Try multiple common TwelveData symbol formats for Brent.
        'crude_futures': ['XBR/USD', 'BRENT'],
    }
    symbols = symbol_map.get(asset_key, [])
    if not symbols:
        return None
    import requests
    from datetime import datetime, timezone
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
    }
    for sym in symbols:
        try:
            url = "https://api.twelvedata.com/time_series"
            for interval in ['15min', '1h']:
                params = {
                    'symbol': sym,
                    'interval': interval,
                    'outputsize': 500,
                    'apikey': TWELVEDATA_API_KEY,
                }
                r = requests.get(url, params=params, headers=headers, timeout=(10, 25))
                if r.status_code != 200:
                    print(f"TwelveData chart {asset_key}: HTTP {r.status_code} for {sym} ({interval})", flush=True)
                    continue
                data = r.json()
                # TwelveData error shape: {"code":..., "message":"...","status":"error"}
                if str(data.get('status', '')).lower() == 'error' or data.get('code'):
                    msg = (data.get('message') or str(data.get('code')) or '').lower()
                    if 'run out of api credits' in msg or 'api credits' in msg or 'rate' in msg:
                        _twelvedata_block_until = time.time() + 65
                        print(f"TwelveData chart {asset_key}: rate limited, pausing fallback for 65s", flush=True)
                        return None
                    print(f"TwelveData chart {asset_key}: {sym} ({interval}) -> {data.get('message') or data.get('code')}", flush=True)
                    continue
                values = data.get('values') or []
                if not values:
                    print(f"TwelveData chart {asset_key}: no values for {sym} ({interval})", flush=True)
                    continue
                candles = []
                # TwelveData usually returns newest-first; reverse for chart rendering.
                for row in reversed(values):
                    dt = row.get('datetime')
                    try:
                        # Handle common datetime formats.
                        if 'T' in dt:
                            ts = int(datetime.fromisoformat(dt.replace('Z', '+00:00')).timestamp())
                        else:
                            ts = int(datetime.strptime(dt, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())
                        o = float(row.get('open', 0))
                        h = float(row.get('high', 0))
                        l_ = float(row.get('low', 0))
                        c = float(row.get('close', 0))
                    except Exception:
                        continue
                    if c > 0:
                        candles.append({
                            'time': ts,
                            'open': round(o, 2),
                            'high': round(h, 2),
                            'low': round(l_, 2),
                            'close': round(c, 2),
                        })
                if candles:
                    print(f"TwelveData chart {asset_key}: {len(candles)} candles from {sym} ({interval})", flush=True)
                    return candles
                else:
                    print(f"TwelveData chart {asset_key}: parsed 0 candles for {sym} ({interval})", flush=True)
        except Exception as e:
            print(f"TwelveData chart fetch {asset_key} ({sym}): {e}", flush=True)
            continue
    return None

def fetch_from_yahoo(symbol_key, yahoo_symbol, max_retries=3):
    """Legacy Yahoo backup fetcher (kept for compatibility; primary backup is TradingView)."""
    import requests
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.tradingview.com/'
    }
    
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=(15, 30))
            if r.status_code == 429:
                wait_time = (attempt + 1) * 2
                print(f"Yahoo Finance rate limited for {symbol_key}, waiting {wait_time}s before retry...", flush=True)
                time.sleep(wait_time)
                continue
            if r.status_code == 200:
                data = r.json()
                if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                    result = data['chart']['result'][0]
                    meta = result.get('meta', {})
                    price = meta.get('regularMarketPrice') or meta.get('previousClose') or meta.get('regularMarketPreviousClose')
                    prev_close = meta.get('previousClose') or meta.get('regularMarketPreviousClose') or price
                    if price and price > 0:
                        change = price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close else 0
                        return {
                            'price': price,
                            'change': change,
                            'change_pct': change_pct,
                            'source': 'yahoo'
                        }
        except requests.exceptions.Timeout as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"Yahoo Finance timeout for {symbol_key} (attempt {attempt+1}/{max_retries}), retrying in {wait_time}s...", flush=True)
                time.sleep(wait_time)
                continue
            else:
                print(f"Yahoo Finance fetch timeout for {symbol_key} ({yahoo_symbol}) after {max_retries} attempts", flush=True)
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                print(f"Yahoo Finance fetch error for {symbol_key} ({yahoo_symbol}): {e}, retrying in {wait_time}s...", flush=True)
                time.sleep(wait_time)
                continue
            else:
                print(f"Yahoo Finance fetch error for {symbol_key} ({yahoo_symbol}): {e}", flush=True)
    # end for attempt
    return None


def fetch_from_tradingview(symbol_key, tv_symbols, max_retries=3):
    """Fetch backup price from TradingView scanner endpoint (non-Yahoo source)."""
    import requests
    url = "https://scanner.tradingview.com/global/scan"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'Origin': 'https://www.tradingview.com',
        'Referer': 'https://www.tradingview.com/',
    }
    for tv_symbol in tv_symbols:
        payload = {
            "symbols": {"tickers": [tv_symbol], "query": {"types": []}},
            "columns": ["close", "change", "change_abs"],
        }
        for attempt in range(max_retries):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=(10, 20))
                if r.status_code == 429:
                    wait_time = (attempt + 1) * 2
                    print(f"TradingView rate limited for {symbol_key} ({tv_symbol}), waiting {wait_time}s...", flush=True)
                    time.sleep(wait_time)
                    continue
                if r.status_code != 200:
                    continue
                data = r.json()
                rows = data.get('data') or []
                if not rows:
                    continue
                vals = rows[0].get('d') or []
                if len(vals) < 3:
                    continue
                price = vals[0]
                pct = vals[1]
                change = vals[2]
                try:
                    price = float(price)
                    change = float(change)
                    pct = float(pct)
                except Exception:
                    continue
                if price > 0:
                    return {
                        'price': price,
                        'change': change,
                        'change_pct': pct,
                        'source': 'tradingview',
                        'tv_symbol': tv_symbol,
                    }
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    time.sleep(wait_time)
                    continue
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"TradingView fetch error for {symbol_key} ({tv_symbol}): {e}, retrying in {wait_time}s...", flush=True)
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"TradingView fetch error for {symbol_key} ({tv_symbol}): {e}", flush=True)
    return None

def fetch_all_backup_prices(force=False):
    """Fetch backup prices from TradingView for all assets (used when IB is disconnected).
    When force=True, overwrite all prices (use when IB has just disconnected)."""
    global live_prices
    fetched_count = 0
    for symbol_key, tv_symbols in BACKUP_TV_SYMBOLS.items():
        if symbol_key == 'silver':
            tv_symbols = get_silver_backup_tv_symbols()
        if symbol_key == 'crude_futures':
            tv_symbols = get_crude_backup_tv_symbols()
        # When force=True (IB just disconnected), always fetch. Otherwise only if IB doesn't have this price.
        if force or symbol_key not in live_prices or live_prices[symbol_key].get('source') != 'ib':
            price_data = fetch_from_tradingview(symbol_key, tv_symbols)
            if price_data:
                live_prices[symbol_key] = price_data
                fetched_count += 1
                src_sym = price_data.get('tv_symbol', 'unknown')
                print(f"Backup price fetched: {symbol_key} = {price_data.get('price', 0):.2f} ({src_sym})", flush=True)
    if fetched_count > 0:
        update_price_cache_from_live()
        print(f"Updated {fetched_count} backup prices from TradingView", flush=True)
    return fetched_count

def run_backup_price_fetcher():
    """Background thread to fetch backup prices from TradingView every 10 seconds"""
    print("Backup price fetcher thread started - will fetch every 10 seconds", flush=True)
    while True:
        try:
            # Always fetch backup prices, but IB prices take priority when available
            # This ensures seamless fallback when IB disconnects
            # TradingView scanner data used as non-Yahoo backup
            fetched = fetch_all_backup_prices()
            if fetched == 0:
                # Log periodically when no prices fetched (every 60 seconds = 6 iterations)
                import random
                if random.randint(1, 6) == 1:  # Log roughly every minute
                    print("Backup fetcher running (IB prices active, backup ready if needed)", flush=True)
            time.sleep(10)  # Update every 10 seconds
        except Exception as e:
            print(f"❌ Backup price fetcher error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            time.sleep(10)

def update_price_cache_from_live():
    """Update the Flask cache from live prices (IB or backup from TradingView)."""
    global live_prices, price_cache

    results = []
    for key, asset in ASSETS.items():
        if key not in live_prices:
            continue
        data = live_prices[key]
        row = {
            'symbol': asset['display_symbol'],
            'regularMarketPrice': data.get('price', 0),
            'regularMarketChange': data.get('change', 0),
            'regularMarketChangePercent': data.get('change_pct', 0),
        }
        if data.get('source'):
            row['priceSource'] = data['source']
        if data.get('contract'):
            row['ibContract'] = data['contract']
        if data.get('tv_symbol'):
            row['backupSymbol'] = data['tv_symbol']
        results.append(row)
    if results:
        with price_cache['lock']:
            price_cache['data'] = {'quoteResponse': {'result': results}}
            price_cache['last_update'] = time.time()

def run_ibkr_connection():
    """Run IBKR connection using ib_insync for real-time prices"""
    global live_prices, ib_connected, _consecutive_failures, ib_subscribed_meta
    
    import asyncio
    import socket
    
    # Create new event loop for this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    print(f"[Wall clock] Market data type: {IB_MARKET_DATA_TYPE} (1=live, 3=delayed). Set WALLCLOCK_MARKET_DATA_TYPE=3 for Dow if you get Error 354.", flush=True)
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
                _consecutive_failures += 1
                # When IB is not reachable, force backup prices so UI shows TradingView/Yahoo data
                if not ib_connected:
                    for k in list(live_prices.keys()):
                        if live_prices[k].get('source') == 'ib':
                            live_prices[k]['source'] = 'yahoo'
                    fetch_all_backup_prices(force=True)
                if _consecutive_failures >= FAILURES_BEFORE_NOTIFY:
                    send_reauth_notification()
                # Log every ~1 min to avoid console spam (retry every 10s)
                if _consecutive_failures == 1 or _consecutive_failures % 6 == 0:
                    print("IB Gateway not reachable - using backup prices from TradingView. Retrying...", flush=True)
                time.sleep(10)
                continue
            
            from ib_insync import IB, Index, Future, Forex
            
            ib = IB()
            print(f"Connecting to IB Gateway on port {connected_port}...", flush=True)
            ib.connect(IB_HOST, connected_port, clientId=IB_CLIENT_ID, timeout=20)
            ib_connected = True
            _consecutive_failures = 0  # Reset so next time we need re-auth we can notify again
            ib_subscribed_meta.clear()
            print("Connected to Interactive Brokers!", flush=True)
            print(f"WALLCLOCK_VERSION={WALLCLOCK_VERSION} WALLCLOCK_BRENT_CHAIN={WALLCLOCK_BRENT_CHAIN}", flush=True)
            # Set market data type immediately (1=live, 3=delayed). Must be before any reqMktData.
            ib.reqMarketDataType(IB_MARKET_DATA_TYPE)
            print(f"Market data type: {IB_MARKET_DATA_TYPE} ({'live' if IB_MARKET_DATA_TYPE == 1 else 'delayed'})", flush=True)
            
            # Front month for ES/NQ; GC and SI use their own cycles
            front_month = get_front_month()
            print(f"ES/NQ front month: {front_month}", flush=True)
            print(f"GC (Gold) contract: {get_gc_contract_month()}, SI (Silver) contract: {get_si_contract_month()}", flush=True)
            
            # Create contracts (Contract used for GC/SI explicit month)
            from ib_insync import Contract
            contracts = {}
            
            # Gold: GC (COMEX), 100 oz. Current continuous = Apr, Jun, Aug, Oct, Dec
            gc_month = get_gc_contract_month()
            gold_contract = Contract()
            gold_contract.symbol = 'GC'
            gold_contract.secType = 'FUT'
            gold_contract.exchange = 'COMEX'
            gold_contract.currency = 'USD'
            gold_contract.lastTradeDateOrContractMonth = gc_month
            gold_contract.multiplier = '100'
            contracts['gold'] = gold_contract
            print(f"GC requesting month: {gc_month} (April continuous)", flush=True)
            
            # Silver: select front contract by IB expiry chain (avoids stale month/settlement mismatches).
            contracts['silver'] = pick_silver_front_contract(ib)
            
            # Futures
            contracts['sp500_futures'] = Future('ES', front_month, 'CME')
            contracts['nasdaq_futures'] = Future('NQ', front_month, 'CME')
            
            # GIFT Nifty - strict front month (Feb now), auto-roll to next month after expiry
            nifty_front = get_nifty_front_month()  # e.g. "202602" in February
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
                    def norm_month(c):
                        raw = (getattr(c.contract, 'lastTradeDateOrContractMonth', '') or '').strip().replace(' ', '')
                        if len(raw) >= 6 and raw[:6].isdigit():
                            return raw[:6]
                        try:
                            if raw.isdigit():
                                return raw[:6]
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
                    # Sort by contract month ascending (Jan, Feb, Mar...)
                    matches_sorted = sorted(matches, key=lambda m: norm_month(m))
                    available_months = [norm_month(m) for m in matches_sorted]
                    print(f"Nifty available months: {available_months}", flush=True)
                    # 1) Prefer exact current month (e.g. 202602 for Feb)
                    for m in matches_sorted:
                        if norm_month(m) == nifty_front:
                            contracts['nifty_futures'] = m.contract
                            nifty_found = True
                            print(f"Nifty using Feb/current month: {m.contract} ({getattr(m.contract, 'lastTradeDateOrContractMonth', '')})", flush=True)
                            break
                    # 2) Only if no current month (e.g. after expiry), use next month
                    if not nifty_found:
                        for m in matches_sorted:
                            if norm_month(m) > nifty_front:
                                contracts['nifty_futures'] = m.contract
                                nifty_found = True
                                print(f"Nifty rolled to next month: {m.contract} ({getattr(m.contract, 'lastTradeDateOrContractMonth', '')})", flush=True)
                                break
                    if not nifty_found and matches_sorted:
                        contracts['nifty_futures'] = matches_sorted[0].contract
                        nifty_found = True
                        print(f"Nifty fallback: {matches_sorted[0].contract}", flush=True)
            except Exception as e:
                print(f"Nifty error: {e}", flush=True)
            if not nifty_found:
                print("Nifty contract not found - skipping", flush=True)
            
            # YM (E-mini Dow): Skip IB subscription - use TradingView backup instead
            print("YM (Dow): Using TradingView backup (skipping IB due to Error 354)", flush=True)
            contracts['hsi_futures'] = Future('HSI', front_month, 'HKFE')
            contracts['btc_futures'] = Future('BRR', front_month, 'CME')  # CME Bitcoin futures
            contracts['eur_usd'] = Forex('EURUSD')
            contracts['usd_jpy'] = Forex('USDJPY')
            contracts['usd_hkd'] = Forex('USDHKD')
            contracts['gbp_usd'] = Forex('GBPUSD')
            contracts['usd_inr'] = Forex('USDINR')
            # Brent crude: front month from IB chain (expiry-based roll), then calendar fallback.
            brent_contract = pick_brent_front_contract(ib)
            if not brent_contract:
                cl_month = get_cl_front_month()
                for cand in get_brent_candidates(cl_month):
                    try:
                        q = ib.qualifyContracts(cand)
                        if q:
                            brent_contract = q[0]
                            print(f"Brent qualified by candidate: {brent_contract}", flush=True)
                            break
                    except Exception as e:
                        print(f"Brent candidate failed {cand}: {e}", flush=True)
            if brent_contract:
                contracts['crude_futures'] = brent_contract
            else:
                print("Brent not qualified on IBKR; using Yahoo fallback for crude_futures", flush=True)
            # Kospi 200: K2I on KRX, quarterly (Mar/Jun/Sep/Dec) = active front month
            contracts['kospi_200'] = Future('K2I', front_month, 'KRX')

            # Optional extras (available to all clients; UI default hides them per-browser)
            contracts['mtw_futures'] = Future('MTW', get_nifty_front_month(), 'HKFE')  # MSCI Taiwan @ HKFE
            contracts['rty_futures'] = Future('RTY', front_month, 'CME')  # Russell 2000
            contracts['nikkei_futures'] = Future('NKD', front_month, 'CME')  # Nikkei USD @ CME
            contracts['dax_futures'] = Future('DAX', front_month, 'EUREX')
            contracts['wti_futures'] = Future('CL', get_cl_front_month(), 'NYMEX')
            contracts['copper_futures'] = Future('HG', get_hg_contract_month(), 'COMEX')

            # Qualify and subscribe
            tickers = {}
            last_valid_tick_time = {}
            for key, contract in contracts.items():
                if contract is None:
                    continue
                try:
                    qualified = ib.qualifyContracts(contract)
                    if qualified:
                        qc = qualified[0]
                        ib_subscribed_meta[key] = {'contract': str(qc)}
                        ticker = ib.reqMktData(qc, '', False, False)
                        tickers[key] = ticker
                        last_valid_tick_time[key] = time.time()
                        print(f"Subscribed: {key} -> {qc}", flush=True)
                    else:
                        print(f"Could not qualify: {key}", flush=True)
                except Exception as e:
                    print(f"Error with {key}: {e}", flush=True)
            # If Nifty didn't qualify, try delayed data type for next connection
            if 'nifty_futures' not in tickers and contracts.get('nifty_futures'):
                print("Nifty: try enabling delayed market data in IBKR for SGX", flush=True)
            
            if 'crude_futures' not in tickers:
                print(
                    "⚠️ CRUDE/BRENT: NOT subscribed on IBKR (qualification failed or no contract). "
                    "Tile uses TradingView backup only — see /api/status priceSource=tradingview. "
                    "Fix: Account → Market Data Subscriptions → ICE Energy / NYMEX Energy for Brent futures.",
                    flush=True,
                )
            else:
                print(
                    "CRUDE/BRENT: Subscribed on IBKR — waiting for bid/ask or last. "
                    "If price stays on TradingView, Brent ticker has no data (permissions or market closed).",
                    flush=True,
                )
            
            print(f"Streaming {len(tickers)} symbols...", flush=True)
            
            # Process updates: periodic reconnect refreshes futures months (Brent roll, etc.).
            # Default daily (WALLCLOCK_CONTRACT_REFRESH_SEC); does NOT switch to backup — see planned_refresh below.
            reconnect_interval = WALLCLOCK_CONTRACT_REFRESH_SEC
            stale_threshold = 15 * 60     # 15 min without updates = force reconnect (was 5 min)
            last_update_time = time.time()
            loop_start = time.time()
            planned_refresh = False
            crude_ib_warn_sent = False
            
            while ib.isConnected():
                ib.sleep(0.1)  # Process events
                now = time.time()
                got_valid_tick = set()
                # One-time warning if IB never delivers Brent ticks (user sees TradingView forever)
                if not crude_ib_warn_sent and (now - loop_start) > 45:
                    crude_ib_warn_sent = True
                    src = (live_prices.get('crude_futures') or {}).get('source')
                    if src != 'ib':
                        if 'crude_futures' in tickers:
                            print(
                                "⚠️ CRUDE/BRENT: IB subscribed but still no price after 45s — "
                                "check NYMEX/ICE energy market data in IBKR; tile stays on TradingView backup.",
                                flush=True,
                            )
                        else:
                            print(
                                "⚠️ CRUDE/BRENT: No IB subscription — tile uses TradingView only.",
                                flush=True,
                            )
                
                for key, ticker in tickers.items():
                    # Prefer live: bid/ask mid (best for futures), then last, then close
                    price = None
                    if ticker.bid and ticker.bid > 0 and ticker.ask and ticker.ask > 0:
                        price = (ticker.bid + ticker.ask) / 2
                    if (price is None or price <= 0) and ticker.last and ticker.last > 0:
                        price = ticker.last
                    # For futures, ticker.close is often prior settle and can look stale/wrong intraday.
                    # Avoid using close for futures; let backup feed take over if live ticks are absent.
                    if (price is None or price <= 0) and key not in {'gold', 'silver', 'sp500_futures', 'nasdaq_futures', 'nifty_futures', 'hsi_futures', 'btc_futures', 'crude_futures', 'kospi_200'} and ticker.close and ticker.close > 0:
                        price = ticker.close
                    
                    if price and price > 0:
                        got_valid_tick.add(key)
                        last_valid_tick_time[key] = now
                        last_update_time = now
                        prev_close = ticker.close if ticker.close and ticker.close > 0 else price
                        change = price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close else 0
                        data = {'price': price, 'change': change, 'change_pct': change_pct, 'source': 'ib'}
                        meta = ib_subscribed_meta.get(key, {})
                        if meta.get('contract'):
                            data['contract'] = meta['contract']
                        live_prices[key] = data
                        update_price_cache_from_live()

                # If a symbol stops receiving valid IB ticks for too long, allow backup feed to take over.
                # This prevents tiles from appearing "stuck" on stale IB values.
                stale_symbol_threshold = 180
                for key in tickers.keys():
                    if key in got_valid_tick:
                        continue
                    last_ok = last_valid_tick_time.get(key, loop_start)
                    if (now - last_ok) >= stale_symbol_threshold:
                        cur = live_prices.get(key, {})
                        if cur.get('source') == 'ib':
                            live_prices[key]['source'] = 'stale_ib'
                            print(
                                f"{key}: IB ticks stale for {int(now - last_ok)}s, allowing backup source refresh",
                                flush=True,
                            )
                
                # Scheduled reconnect: re-subscribe all futures on new front months (Brent roll, etc.)
                if (now - loop_start) >= reconnect_interval:
                    print(
                        f"Scheduled contract refresh ({reconnect_interval}s) — reconnecting to IB (no backup switch)...",
                        flush=True,
                    )
                    planned_refresh = True
                    break
                # Reconnect if no updates for too long (connection may be stale)
                if (now - last_update_time) >= stale_threshold and price_cache['last_update']:
                    print("No price updates for 15 min - reconnecting...", flush=True)
                    planned_refresh = False
                    break
            
            if planned_refresh:
                try:
                    ib.disconnect()
                except Exception:
                    pass
                print("Contract refresh: reconnecting to IBKR immediately...", flush=True)
                time.sleep(1)
                continue
            
            print("IB connection lost - switching to backup prices from TradingView", flush=True)
            ib_connected = False
            # Clear source so background fetcher will keep updating; then force-fetch backup prices now
            for k in list(live_prices.keys()):
                if live_prices[k].get('source') == 'ib':
                    live_prices[k]['source'] = 'yahoo'  # so background fetcher will refresh
            print("Fetching backup prices immediately (force=True)...", flush=True)
            fetched = fetch_all_backup_prices(force=True)
            print(f"Fetched {fetched} backup prices after IB disconnect", flush=True)
            # Send notification email (always on disconnect, throttled by NOTIFY_THROTTLE)
            print("Sending email notification...", flush=True)
            send_reauth_notification()
            
        except Exception as e:
            print(f"IBKR error: {type(e).__name__}: {e}", flush=True)
            ib_connected = False
            # Clear source and force-fetch backup prices when IB fails
            for k in list(live_prices.keys()):
                if live_prices[k].get('source') == 'ib':
                    live_prices[k]['source'] = 'yahoo'
            print("IB error detected - fetching backup prices (force=True)...", flush=True)
            fetched = fetch_all_backup_prices(force=True)
            print(f"Fetched {fetched} backup prices after IB error", flush=True)
        
        _consecutive_failures += 1
        if _consecutive_failures >= FAILURES_BEFORE_NOTIFY:
            send_reauth_notification()
        
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

# Start backup price fetcher from TradingView
def start_backup_price_fetcher():
    global backup_price_thread
    if 'backup_price_thread' not in globals() or backup_price_thread is None or not backup_price_thread.is_alive():
        backup_price_thread = threading.Thread(target=run_backup_price_fetcher, daemon=True)
        backup_price_thread.start()
        print("Backup price fetcher (TradingView) started for all assets", flush=True)
        print("Note: TradingView Premium uses Yahoo Finance data, so you're getting TradingView-quality prices", flush=True)

start_backup_price_fetcher()


def _news_refresh_loop():
    """Refresh news cache every minute so breaking news appears quickly."""
    try:
        _fetch_news_into_cache()
    except Exception as e:
        print(f"News initial fetch: {e}", flush=True)
    while True:
        try:
            time.sleep(NEWS_CACHE_TTL)
            _fetch_news_into_cache()
        except Exception as e:
            print(f"News background refresh error: {e}", flush=True)

# Log email config at startup so you can verify on server (e.g. journalctl -u wallclock)
if NOTIFY_EMAIL:
    smtp_ok = bool(SMTP_USER and SMTP_PASS)
    print(f"Disconnect email: NOTIFY_EMAIL={NOTIFY_EMAIL}, SMTP configured={smtp_ok}", flush=True)
else:
    print("Disconnect email: NOTIFY_EMAIL not set", flush=True)

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'))

@app.route('/logo.png')
def logo():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logo.png'), mimetype='image/png')

@app.route('/manifest.json')
def manifest():
    return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifest.json'), mimetype='application/json')

def _source_from_url(url):
    u = url.lower()
    if 'news.google' in u or 'google.com/rss' in u: return 'Google News'
    if 'reuters' in u or 'feedcontrol' in u: return 'Reuters'
    if 'cnbctv18' in u: return 'CNBC TV18'
    if 'investing.com' in u: return 'Investing.com'
    if 'bloomberg' in u: return 'Bloomberg'
    if 'yahoo' in u: return 'Yahoo Finance'
    if 'cnbc' in u: return 'CNBC'
    if 'dowjones' in u or 'mw_' in u: return 'MarketWatch'
    if 'bbci' in u or 'bbc.' in u: return 'BBC News'
    if 'npr' in u: return 'NPR'
    return 'RSS'


def _tag_local(elem):
    """Return tag name without namespace (e.g. 'item' from '{http://...}item')."""
    if elem is None:
        return ''
    t = elem.tag
    return t.split('}')[-1] if t and '}' in t else (t or '')


def _parse_feed_xml(root, url):
    """Extract articles from RSS/Atom XML root. Handles namespaced and non-namespaced feeds."""
    from datetime import datetime
    # Find item/entry elements (with or without namespace)
    items = list(root.findall('.//item')) or list(root.findall('.//{http://www.w3.org/2005/Atom}entry'))
    if not items:
        items = [e for e in root.iter() if _tag_local(e) in ('item', 'entry')]
    source = _source_from_url(url)
    out = []
    for item in items:
        title_el = item.find('title') or item.find('{http://www.w3.org/2005/Atom}title')
        if title_el is None:
            for c in item:
                if _tag_local(c) == 'title':
                    title_el = c
                    break
        link_el = item.find('link') or item.find('{http://www.w3.org/2005/Atom}link')
        if link_el is None:
            for c in item:
                if _tag_local(c) == 'link':
                    link_el = c
                    break
        link = ''
        if link_el is not None:
            link = link_el.get('href') or (link_el.text or '').strip()
        title = (title_el.text or '').strip() if title_el is not None else ''
        if not title and title_el is not None and hasattr(title_el, 'text'):
            title = (title_el.text or '').strip()
        if not title:
            continue
        pub_el = item.find('pubDate') or item.find('published') or item.find('{http://www.w3.org/2005/Atom}published')
        if pub_el is None:
            for c in item:
                if _tag_local(c) in ('pubDate', 'published'):
                    pub_el = c
                    break
        published_ts = 0
        if pub_el is not None and getattr(pub_el, 'text', None):
            try:
                from email.utils import parsedate_to_datetime
                published_ts = int(parsedate_to_datetime(pub_el.text.strip()).timestamp())
            except Exception:
                try:
                    published_ts = int(datetime.fromisoformat(pub_el.text.replace('Z', '+00:00')).timestamp())
                except Exception:
                    pass
        out.append({'title': title[:300], 'link': (link or '')[:500], 'source': source, 'published_ts': published_ts})
    return out


def _parse_rss_feed(url):
    """Fetch one RSS/Atom feed. Use requests first (better for server IPs), then urllib fallback."""
    articles = []
    # 1) Try requests first (many sites block urllib / default Python)
    try:
        import requests
        r = requests.get(url, headers=NEWS_HEADERS, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            print(f"News feed {url[:55]}... HTTP {r.status_code}", flush=True)
        else:
            raw = r.content[:500] if r.content else b''
            if raw.lstrip().startswith(b'<!') or b'<html' in raw.lower():
                print(f"News feed {url[:55]}... 200 but HTML (not RSS), skip", flush=True)
            else:
                root = ElementTree.fromstring(r.content)
                articles = _parse_feed_xml(root, url)
                if not articles:
                    print(f"News feed {url[:55]}... 200 OK but 0 items parsed", flush=True)
    except Exception as e:
        print(f"News feed {url[:55]}... requests error: {e}", flush=True)
    # 2) Fallback: urllib
    if not articles:
        try:
            req = Request(url, headers=NEWS_HEADERS)
            with urlopen(req, timeout=15) as resp:
                tree = ElementTree.parse(resp)
                articles = _parse_feed_xml(tree.getroot(), url)
        except (URLError, ElementTree.ParseError, OSError) as e:
            print(f"News feed {url[:55]}... urllib error: {e}", flush=True)
    return articles

def _news_relevance_score(article):
    """Score article by relevance to our assets (title). Higher = more relevant to price-moving news."""
    title = (article.get('title') or '').lower()
    return sum(1 for kw in NEWS_ASSET_KEYWORDS if kw.lower() in title)


def _fetch_news_into_cache():
    """Fetch all feeds, merge, dedupe, keep last 24h, prioritize articles that impact our assets."""
    global NEWS_CACHE
    all_articles = []
    for url in NEWS_FEEDS:
        all_articles.extend(_parse_rss_feed(url))
    seen_links = set()
    unique = []
    for a in sorted(all_articles, key=lambda x: -x['published_ts']):
        if a['link'] and a['link'] not in seen_links:
            seen_links.add(a['link'])
            unique.append(a)
    now_ts = time.time()
    cutoff = now_ts - NEWS_MAX_AGE_HOURS * 3600
    within_24h = [a for a in unique if a.get('published_ts', 0) == 0 or a['published_ts'] >= cutoff]
    filtered = within_24h or unique
    # Sort by date/time: latest first (newest at top). Use relevance as tie-breaker for same time.
    filtered.sort(key=lambda a: (-a.get('published_ts', 0), -_news_relevance_score(a)))
    with NEWS_LOCK:
        NEWS_CACHE['articles'] = filtered[:NEWS_MAX_STORED]
        NEWS_CACHE['updated'] = time.time()
    print(f"News: {min(len(filtered), NEWS_MAX_STORED)} articles in cache (from {len(all_articles)} fetched)", flush=True)


def _start_news_thread():
    _news_thread = threading.Thread(target=_news_refresh_loop, daemon=True)
    _news_thread.start()
    print("News: background refresh every 1 min", flush=True)


_start_news_thread()

@app.route('/api/news')
def api_news():
    # Paginated market news: limit (default 10), offset (default 0). Max 10 per page.
    limit = min(10, max(1, int(request.args.get('limit', 10))))
    offset = max(0, int(request.args.get('offset', 0)))
    now = time.time()
    with NEWS_LOCK:
        articles = list(NEWS_CACHE.get('articles', []))
        updated = NEWS_CACHE.get('updated', 0)
    if not articles or (now - updated) > NEWS_CACHE_TTL:
        print("News: refreshing cache (fetching from feeds)...", flush=True)
        try:
            _fetch_news_into_cache()
            with NEWS_LOCK:
                articles = list(NEWS_CACHE.get('articles', []))
        except Exception as e:
            print(f"News fetch error: {e}", flush=True)
    page = articles[offset:offset + limit]
    out = {
        'articles': page,
        'total': len(articles),
        'offset': offset,
        'limit': limit,
        'has_more': offset + len(page) < len(articles),
    }
    if len(articles) == 0:
        out['message'] = 'No articles yet. Feeds may be temporarily unavailable or blocked from server. Check server logs: journalctl -u wallclock -n 50 | grep -i news'
    return jsonify(out)

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
    # Only live prices; no demo. Include wallclock meta so you can verify deploy (version, Brent chain).
    if data:
        payload = dict(data)
        payload['wallclock'] = {
            'version': WALLCLOCK_VERSION,
            'brent_chain': WALLCLOCK_BRENT_CHAIN,
            'ib_connected': ib_connected,
        }
        resp = jsonify(payload)
    else:
        resp = jsonify({
            'quoteResponse': {'result': []},
            'wallclock': {
                'version': WALLCLOCK_VERSION,
                'brent_chain': WALLCLOCK_BRENT_CHAIN,
                'ib_connected': ib_connected,
            },
        })
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['X-WallClock-Version'] = WALLCLOCK_VERSION
    return resp

@app.route('/api/chart/<asset_key>')
def api_chart(asset_key):
    # OHLC for 15-min candlestick chart, 6 months. When IBKR disconnected use Yahoo first so charts load.
    if asset_key not in CHART_SYMBOLS and asset_key not in CHART_IBKR_ASSETS and asset_key != 'ym_futures':
        return jsonify({'error': 'Unknown asset', 'candles': []}), 404
    # Brent chart should use IBKR first, then TwelveData (non-Yahoo) fallback.
    allow_yahoo_fallback = asset_key != 'crude_futures'
    source = 'none'
    candles = None
    if ib_connected and asset_key in CHART_IBKR_ASSETS:
        candles = fetch_chart_from_ibkr(asset_key)
        if candles:
            source = 'ibkr'
    # Non-Yahoo fallback for Brent chart
    if (candles is None or len(candles) == 0) and asset_key == 'crude_futures':
        candles = fetch_chart_from_twelvedata(asset_key)
        if candles:
            source = 'twelvedata'
    if (candles is None or len(candles) == 0) and allow_yahoo_fallback:
        candles = fetch_chart_from_yahoo(asset_key)
        if candles:
            source = 'yahoo'
    if (candles is None or len(candles) == 0) and asset_key in CHART_IBKR_ASSETS:
        candles = fetch_chart_from_ibkr(asset_key)
        if candles:
            source = 'ibkr'
    if not candles:
        # Return 200 so frontend can show message; include error for display
        return jsonify({
            'symbol': CHART_SYMBOLS.get(asset_key, asset_key),
            'source': 'none',
            'interval': '15m',
            'range': '6mo',
            'candles': [],
            'error': 'Chart data unavailable (IBKR, TwelveData/Yahoo failed). Check server logs.',
        })
    # Align fallback chart level to current live price while preserving candlestick pattern.
    # For non-IBKR sources (e.g., TwelveData spot proxies), apply a constant offset to
    # the whole visible series so the last candle matches the live futures tile.
    try:
        lp = live_prices.get(asset_key, {})
        live_px = lp.get('price')
        if isinstance(live_px, (int, float)) and live_px > 0 and candles:
            last = candles[-1]
            old_close = last.get('close')
            if isinstance(old_close, (int, float)) and old_close > 0:
                # If mismatch is meaningful and source is fallback, shift the whole series.
                if abs(live_px - old_close) / old_close > 0.001:  # >0.1%
                    if source != 'ibkr':
                        shift = float(live_px) - float(old_close)
                        shifted = []
                        for c in candles:
                            o = float(c.get('open', 0)) + shift
                            h = float(c.get('high', 0)) + shift
                            l_ = float(c.get('low', 0)) + shift
                            cl = float(c.get('close', 0)) + shift
                            # Keep OHLC internally consistent after shift
                            hi = max(h, o, cl, l_)
                            lo = min(l_, o, cl, h)
                            shifted.append({
                                'time': c.get('time'),
                                'open': round(o, 2),
                                'high': round(hi, 2),
                                'low': round(lo, 2),
                                'close': round(cl, 2),
                            })
                        candles = shifted
                        print(f"Chart sync {asset_key}: shifted series by {shift:.2f} ({source})", flush=True)
                    else:
                        # IBKR should already match live; keep only a tiny close sync guard.
                        last['close'] = round(float(live_px), 2)
                        last['high'] = round(max(float(last.get('high', live_px)), float(live_px)), 2)
                        last['low'] = round(min(float(last.get('low', live_px)), float(live_px)), 2)
                        candles[-1] = last
                        print(f"Chart sync {asset_key}: adjusted IBKR last close {old_close} -> {live_px}", flush=True)
    except Exception as e:
        print(f"Chart sync {asset_key}: {e}", flush=True)
    symbol = CHART_SYMBOLS.get(asset_key, asset_key)
    return jsonify({
        'symbol': symbol,
        'source': source,
        'interval': '15m',
        'range': '6mo',
        'candles': candles,
    })

@app.route('/api/status')
def api_status():
    crude = live_prices.get('crude_futures', {})
    return jsonify({
        'version': WALLCLOCK_VERSION,
        'brent_chain': WALLCLOCK_BRENT_CHAIN,
        'ib_connected': ib_connected,
        'notify_throttle_hours': NOTIFY_THROTTLE_HOURS,
        'prices_count': len(live_prices),
        'last_update': price_cache['last_update'],
        'crude_futures': {
            'price': crude.get('price'),
            'priceSource': crude.get('source'),
            'ibContract': crude.get('contract'),
            'backupSymbol': crude.get('tv_symbol'),
        },
    })

@app.route('/api/test-notification')
@app.route('/test-notification')  # alternate in case proxy strips /api
def api_test_notification():
    """Send a test disconnect email to verify SMTP/notify config. Does not affect real throttle."""
    ok, msg = send_test_notification()
    if ok:
        return jsonify({'ok': True, 'message': msg})
    return jsonify({'ok': False, 'error': msg}), 400

@app.route('/api/sources')
def api_sources():
    """Which IBKR tickers/contracts we use for each asset."""
    return jsonify({
        'gold': 'GC (COMEX Gold, 100 oz, contract months Feb/Apr/Jun/Aug/Oct/Dec, auto-roll)',
        'silver': 'SI (COMEX Silver, front = nearest listed expiry >= today on IB chain)',
        'nifty_futures': 'NIFTY (GIFT Nifty, SGX, front month = current month, auto-roll after expiry)',
        'ym_futures': 'YM (Dow E-mini futures, from TradingView backup)',
        'hsi_futures': 'HSI (Hang Seng Index futures, HKFE)',
        'btc_futures': 'BRR (CME Bitcoin futures)',
        'crude_futures': 'Brent Futures (IBKR: front = nearest listed expiry >= today on BZ@NYMEX→COIL@ICE chain; daily resubscribe)',
        'kospi_200': 'K2I (KOSPI 200 futures, KRX, quarterly)',
        'dollar_index': 'DXY (US Dollar Index, TradingView backup)',
        'eur_usd': 'EURUSD (FX spot via IBKR CASH/IDEALPRO, backup from TradingView)',
        'usd_jpy': 'USDJPY (FX spot via IBKR CASH/IDEALPRO, backup from TradingView)',
        'usd_hkd': 'USDHKD (FX spot via IBKR CASH/IDEALPRO, backup from TradingView)',
        'gbp_usd': 'GBPUSD (FX spot via IBKR CASH/IDEALPRO, backup from TradingView)',
        'usd_inr': 'USDINR (FX spot via IBKR CASH/IDEALPRO, backup from TradingView)',
        'mtw_futures': 'MTW (MSCI Taiwan Index futures, HKFE)',
        'rty_futures': 'RTY (E-mini Russell 2000, CME)',
        'nikkei_futures': 'NKD (Nikkei 225 USD futures, CME)',
        'dax_futures': 'DAX / FDAX (EUREX)',
        'wti_futures': 'CL (WTI Crude Oil, NYMEX)',
        'copper_futures': 'HG (Copper, COMEX)',
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
