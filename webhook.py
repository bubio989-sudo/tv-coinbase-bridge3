import os
import time
import json
from datetime import datetime
from flask import Flask, request, jsonify
import requests
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

app = Flask(__name__)

# ====== Config ======
CB_API_KEY_NAME = os.environ.get("CB_API_KEY_NAME", "")
CB_PRIVATE_KEY = os.environ.get("CB_PRIVATE_KEY", "").replace("\\n", "\n")
USE_TEN_PERCENT = os.environ.get("USE_TEN_PERCENT", "true").lower() == "true"
CB_BASE_URL = "https://api.coinbase.com"

ENDPOINT_ACCOUNTS = "/api/v3/brokerage/accounts"
ENDPOINT_PRODUCTS = "/api/v3/brokerage/products"
ENDPOINT_TICKER = "/api/v3/brokerage/products/{product_id}/ticker"
ENDPOINT_ORDER = "/api/v3/brokerage/orders"

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def build_jwt(request_method, request_path):
    """Build JWT token for Coinbase Advanced Trade API"""
    try:
        # Load private key
        private_key = serialization.load_pem_private_key(
            CB_PRIVATE_KEY.encode('utf-8'),
            password=None,
            backend=default_backend()
        )
        
        # Build JWT payload
        jwt_payload = {
            'sub': CB_API_KEY_NAME,
            'iss': "coinbase-cloud",
            'nbf': int(time.time()),
            'exp': int(time.time()) + 120,
            'uri': f"{request_method} {CB_BASE_URL}{request_path}",
        }
        
        # Sign JWT
        jwt_token = jwt.encode(
            jwt_payload,
            private_key,
            algorithm='ES256',
            headers={'kid': CB_API_KEY_NAME, 'nonce': str(int(time.time() * 1000))}
        )
        
        return jwt_token
    except Exception as e:
        log(f"❌ JWT generation failed: {e}")
        raise

def cb_request(method, endpoint, params=None, body=None):
    """Make authenticated request to Coinbase Advanced Trade API"""
    url = f"{CB_BASE_URL}{endpoint}"
    
    # Build request path for JWT
    request_path = endpoint
    if params:
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        request_path = f"{endpoint}?{query_string}"
    
    jwt_token = build_jwt(method.upper(), request_path)
    
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
    }
    
    log(f"📡 {method.upper()} {url}")
    
    try:
        if method.upper() == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=15)
        else:
            r = requests.post(url, headers=headers, json=body, timeout=20)
        
        log(f"📥 Status: {r.status_code}")
        
        if r.status_code >= 400:
            log(f"❌ Error: {r.text}")
            raise Exception(f"Coinbase API error {r.status_code}: {r.text}")
        
        return r.json()
    except Exception as e:
        log(f"❌ Request failed: {e}")
        raise

def parse_kv(raw):
    """Parse TradingView alert format"""
    data = {}
    for part in raw.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            data[k.strip().lower()] = v.strip()
    return data

def get_product_info(product_id):
    """Get product details"""
    try:
        res = cb_request("GET", ENDPOINT_PRODUCTS, params={"product_ids": product_id})
        for p in res.get("products", []):
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
    """Get current price"""
    data = cb_request("GET", ENDPOINT_TICKER.replace("{product_id}", product_id))
    price = data.get("price") or data.get("best_ask") or data.get("best_bid")
    return float(price)

def get_balances():
    """Get account balances"""
    balances = {}
    cursor = None
    
    for _ in range(10):  # Max 10 pages
        params = {"cursor": cursor} if cursor else {}
        res = cb_request("GET", ENDPOINT_ACCOUNTS, params=params)
        
        for acct in res.get("accounts", []):
            currency = acct.get("currency")
            available = float(acct.get("available_balance", {}).get("value", 0))
            if available > 0:
                balances[currency] = balances.get(currency, 0.0) + available
        
        if not res.get("has_next"):
            break
        cursor = res.get("cursor")
    
    return balances

def round_to_increment(value, increment):
    """Round down to increment"""
    return round(int(value / increment) * increment, 10)

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
    log(f"✅ Buy order: {result.get('order_id')}")
    return result

def place_market_sell(product_id, use_ten_percent, usd_amount):
    """Place market sell order"""
    base_currency = product_id.split("-")[0]
    balances = get_balances()
    base_available = balances.get(base_currency, 0.0)
    
    if base_available <= 0:
        raise Exception(f"No {base_currency} to sell")
    
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
    log(f"✅ Sell order: {result.get('order_id')}")
    return result

@app.route("/webhook", methods=["POST"])
def webhook():
    """TradingView webhook"""
    try:
        log("=" * 60)
        log("📨 Webhook received")
        
        raw = request.get_data(as_text=True)
        log(f"Raw: {raw}")
        
        data = parse_kv(raw)
        log(f"Parsed: {data}")
        
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
    """Health check"""
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
            balance_html = "<br>".join([f"<strong>{k}:</strong> {v:.8f}" for k, v in list(balances.items())[:10]])
            balance_section = f'<div style="background:#e8f5e9;padding:20px;border-radius:10px;margin:20px 0"><h3>💰 Balances</h3>{balance_html}</div>'
        else:
            balance_section = '<p style="color:orange">⚠️ No balances (account empty)</p>'
    except Exception as e:
        balance_section = f'<p style="color:red">⚠️ Error: {str(e)}</p>'
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>TradingView → Coinbase</title>
        <style>
            body{{font-family:Arial;max-width:900px;margin:50px auto;padding:20px;background:linear-gradient(135deg,#667eea,#764ba2)}}
            .container{{background:white;padding:40px;border-radius:20px;box-shadow:0 10px 40px rgba(0,0,0,0.2)}}
            h1{{color:#0052FF;text-align:center}}
            .status{{color:#00C853;font-weight:bold;font-size:1.3em}}
            code{{background:#f0f0f0;padding:5px 10px;border-radius:5px}}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔷 TradingView → Coinbase</h1>
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
    log("🚀 Starting webhook server")
    log(f"API Key Name present: {bool(CB_API_KEY_NAME)}")
    log(f"Private Key present: {bool(CB_PRIVATE_KEY)}")
    
    if CB_API_KEY_NAME and CB_PRIVATE_KEY:
        try:
            balances = get_balances()
            log("✅ Connected to Coinbase!")
            for currency, amount in balances.items():
                log(f"   {currency}: {amount}")
        except Exception as e:
            log(f"⚠️ Connection test failed: {e}")
    else:
        log("⚠️ API credentials not set!")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
