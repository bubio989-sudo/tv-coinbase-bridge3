import os
import hmac
import hashlib
import time
import json
from datetime import datetime
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# ====== Config (set in Render env vars) ======
CB_API_KEY = os.environ.get("CB_API_KEY", "")
CB_API_SECRET = os.environ.get("CB_API_SECRET", "")
USE_TEN_PERCENT = os.environ.get("USE_TEN_PERCENT", "true").lower() == "true"
CB_BASE_URL = "https://api.coinbase.com"

# Advanced Trade endpoints
ENDPOINT_ACCOUNTS = "/api/v3/brokerage/accounts"
ENDPOINT_PRODUCTS = "/api/v3/brokerage/products"
ENDPOINT_TICKER = "/api/v3/brokerage/products/{product_id}/ticker"
ENDPOINT_ORDER = "/api/v3/brokerage/orders"

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def coinbase_headers(method, path, body):
    """Generate Coinbase Advanced Trade auth headers (Legacy API)"""
    timestamp = str(int(time.time()))
    body_str = json.dumps(body) if body else ""
    message = f"{timestamp}{method.upper()}{path}{body_str}"
    
    log(f"🔐 Signing: {message[:100]}...")
    
    signature = hmac.new(
        CB_API_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return {
        "CB-ACCESS-KEY": CB_API_KEY,
        "CB-ACCESS-SIGN": signature,
        "CB-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }

def cb_request(method, endpoint, params=None, body=None):
    """Make authenticated request to Coinbase Advanced Trade API"""
    url = f"{CB_BASE_URL}{endpoint}"
    headers = coinbase_headers(method, endpoint, body)
    
    log(f"📡 {method} {url}")
    log(f"Headers: CB-ACCESS-KEY={CB_API_KEY[:8]}... CB-ACCESS-TIMESTAMP={headers['CB-ACCESS-TIMESTAMP']}")
    
    try:
        if method.upper() == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=15)
        else:
            r = requests.post(url, headers=headers, json=body, timeout=20)
        
        log(f"📥 Response: {r.status_code}")
        
        if r.status_code >= 400:
            log(f"❌ Error response: {r.text}")
        
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        log(f"❌ HTTP Error: {e}")
        log(f"Response body: {e.response.text if e.response else 'No response'}")
        raise
    except Exception as e:
        log(f"❌ Request error: {e}")
        raise

def parse_kv(raw):
    """Parse key:value; key:value format from TradingView"""
    data = {}
    for part in raw.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            data[k.strip().lower()] = v.strip()
    return data

def get_product_info(product_id):
    """Get product precision and limits"""
    try:
        products = cb_request("GET", ENDPOINT_PRODUCTS, params={"product_ids": product_id})
        for p in products.get("products", []):
            if p.get("product_id") == product_id:
                return {
                    "base_increment": float(p.get("base_increment", "0.00000001")),
                    "quote_increment": float(p.get("quote_increment", "0.01")),
                    "min_market_funds": float(p.get("min_market_funds", "1"))
                }
    except:
        pass
    return {"base_increment": 1e-8, "quote_increment": 0.01, "min_market_funds": 1.0}

def get_ticker_price(product_id):
    """Get current market price"""
    data = cb_request("GET", ENDPOINT_TICKER.replace("{product_id}", product_id))
    price = data.get("price") or data.get("best_ask") or data.get("best_bid")
    return float(price)

def get_balances():
    """Get all account balances"""
    balances = {}
    cursor = None
    attempt = 0
    
    while attempt < 5:  # Prevent infinite loops
        attempt += 1
        params = {}
        if cursor:
            params["cursor"] = cursor
        
        res = cb_request("GET", ENDPOINT_ACCOUNTS, params=params)
        
        for acct in res.get("accounts", []):
            currency = acct.get("currency")
            available = float(acct.get("available_balance", {}).get("value", 0))
            if available > 0:
                balances[currency] = balances.get(currency, 0.0) + available
        
        cursor = res.get("cursor")
        has_next = res.get("has_next", False)
        
        if not has_next or not cursor:
            break
    
    return balances

def round_to_increment(value, increment):
    """Round down to nearest increment"""
    steps = int(value / increment)
    return round(steps * increment, 10)

def place_market_buy(product_id, usd_amount, use_ten_percent):
    """Place market buy order"""
    balances = get_balances()
    usd_available = balances.get("USD", 0.0)
    
    if use_ten_percent:
        funds = usd_available * 0.10
    else:
        funds = float(usd_amount or 0.0)
    
    if funds <= 0:
        raise Exception(f"Insufficient USD. Available: ${usd_available:.2f}")
    
    info = get_product_info(product_id)
    funds = max(funds, info["min_market_funds"])
    funds = round_to_increment(funds, info["quote_increment"])
    
    log(f"📤 BUY ${funds:.2f} of {product_id}")
    
    body = {
        "client_order_id": f"tv_buy_{int(time.time())}",
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": str(funds)
            }
        }
    }
    
    result = cb_request("POST", ENDPOINT_ORDER, body=body)
    log(f"✅ Buy order placed! ID: {result.get('order_id')}")
    return result

def place_market_sell(product_id, use_ten_percent, usd_amount):
    """Place market sell order"""
    base_currency = product_id.split("-")[0]
    balances = get_balances()
    base_available = balances.get(base_currency, 0.0)
    
    if base_available <= 0:
        raise Exception(f"No {base_currency} to sell. Balance: {base_available}")
    
    if use_ten_percent:
        size = base_available * 0.10
    else:
        if usd_amount:
            price = get_ticker_price(product_id)
            size = float(usd_amount) / price
        else:
            size = base_available
    
    info = get_product_info(product_id)
    size = round_to_increment(size, info["base_increment"])
    
    if size <= 0:
        raise Exception("Sell size too small")
    
    log(f"📤 SELL {size} {base_currency}")
    
    body = {
        "client_order_id": f"tv_sell_{int(time.time())}",
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {
            "market_market_ioc": {
                "base_size": str(size)
            }
        }
    }
    
    result = cb_request("POST", ENDPOINT_ORDER, body=body)
    log(f"✅ Sell order placed! ID: {result.get('order_id')}")
    return result

@app.route("/webhook", methods=["POST"])
def webhook():
    """TradingView webhook endpoint"""
    try:
        log("=" * 60)
        log("📨 Webhook received from TradingView")
        
        raw = request.get_data(as_text=True)
        log(f"Raw: {raw}")
        
        data = parse_kv(raw)
        log(f"✅ Parsed: {data}")
        
        product_id = data.get("symbol", "BTC-USD")
        action = (data.get("action") or "buy").lower()
        amount = data.get("amount")
        amount = float(amount) if amount else None
        
        if action == "buy":
            result = place_market_buy(product_id, amount, USE_TEN_PERCENT)
        elif action in ("sell", "close", "exit"):
            result = place_market_sell(product_id, USE_TEN_PERCENT, amount)
        else:
            raise Exception(f"Unknown action: {action}")
        
        log("=" * 60)
        return jsonify({"status": "success", "result": result}), 200
        
    except Exception as e:
        log(f"❌ Error: {str(e)}")
        log("=" * 60)
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    try:
        balances = get_balances()
        return jsonify({"status": "ok", "balances": balances}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/", methods=["GET"])
def home():
    """Home page"""
    try:
        balances = get_balances()
        if balances:
            balance_html = "<br>".join([f"<strong>{k}:</strong> {v}" for k, v in list(balances.items())[:10]])
            balance_section = f'<div style="background:#e8f5e9;padding:20px;border-radius:10px;margin:20px 0"><h3>💰 Balances</h3>{balance_html}</div>'
        else:
            balance_section = '<p style="color:orange">⚠️ No balances found (account may be empty)</p>'
    except Exception as e:
        balance_section = f'<p style="color:red">⚠️ Could not fetch balances.<br>Error: {str(e)}</p>'
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TradingView → Coinbase Bridge</title>
        <style>
            body{{font-family:Arial;max-width:900px;margin:50px auto;padding:20px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)}}
            .container{{background:white;padding:40px;border-radius:20px;box-shadow:0 10px 40px rgba(0,0,0,0.2)}}
            h1{{color:#0052FF;text-align:center}}
            .status{{color:#00C853;font-weight:bold;font-size:1.3em}}
            code{{background:#f0f0f0;padding:5px 10px;border-radius:5px}}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔷 TradingView → Coinbase Advanced</h1>
            <p style="text-align:center">Status: <span class="status">● ONLINE</span></p>
            {balance_section}
            <div style="background:#e3f2fd;padding:20px;border-radius:10px;margin:15px 0">
                <h3>📡 Endpoints</h3>
                <p><strong>Webhook:</strong> <code>POST /webhook</code></p>
                <p><strong>Health:</strong> <code>GET /health</code></p>
            </div>
            <div style="background:#fff3e0;padding:15px;border-radius:8px">
                <h3>📝 Alert Format</h3>
                <code>symbol: BTC-USD; action: buy; amount: 10</code>
            </div>
        </div>
    </body>
    </html>
    """

if __name__ == "__main__":
    log("🚀 Starting TradingView → Coinbase Bridge")
    log(f"API Key present: {bool(CB_API_KEY)}")
    log(f"API Secret present: {bool(CB_API_SECRET)}")
    
    if CB_API_KEY and CB_API_SECRET:
        log("✅ API credentials loaded")
        try:
            balances = get_balances()
            log("✅ Connected to Coinbase!")
            for currency, amount in balances.items():
                log(f"   {currency}: {amount}")
        except Exception as e:
            log(f"⚠️ Could not fetch balances: {e}")
    else:
        log("⚠️ API credentials not set in environment variables!")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
