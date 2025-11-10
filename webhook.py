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
CB_PRIVATE_KEY = os.environ.get("CB_PRIVATE_KEY", "")
USE_TEN_PERCENT = os.environ.get("USE_TEN_PERCENT", "true").lower() == "true"
CB_BASE_URL = "https://api.coinbase.com"

# Coinbase Advanced Trade API endpoints
ENDPOINT_ACCOUNTS = "/api/v3/brokerage/accounts"
ENDPOINT_PRODUCTS = "/api/v3/brokerage/products"
ENDPOINT_TICKER = "/api/v3/brokerage/products/{product_id}/ticker"
ENDPOINT_ORDER = "/api/v3/brokerage/orders"

def log(msg):
    """Simple timestamped console logger"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

# ====== JWT Builder ======
def build_jwt(request_method, request_path):
    """Creates a JWT token for Coinbase Advanced Trade API"""
    try:
        private_key_str = CB_PRIVATE_KEY

        # Normalize line breaks and quotes
        if "\\n" in private_key_str:
            private_key_str = private_key_str.replace("\\n", "\n")
        private_key_str = private_key_str.strip().strip('"').strip("'")

        if not private_key_str.startswith("-----BEGIN"):
            raise Exception("Private key must start with -----BEGIN EC PRIVATE KEY-----")
        if not private_key_str.endswith("-----END EC PRIVATE KEY-----"):
            raise Exception("Private key must end with -----END EC PRIVATE KEY-----")

        # Load private key
        private_key = serialization.load_pem_private_key(
            private_key_str.encode("utf-8"),
            password=None,
            backend=default_backend()
        )

        # JWT payload
        payload = {
            "sub": CB_API_KEY_NAME,
            "iss": "coinbase-cloud",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": f"{request_method} {CB_BASE_URL}{request_path}",
        }

        # Sign JWT
        token = pyjwt.encode(
            payload,
            private_key,
            algorithm="ES256",
            headers={"kid": CB_API_KEY_NAME, "nonce": str(int(time.time() * 1000))}
        )

        return token
    except Exception as e:
        log(f"❌ JWT generation failed: {e}")
        log(f"Private key length: {len(CB_PRIVATE_KEY)}")
        log(f"Private key first 50 chars: {CB_PRIVATE_KEY[:50]}")
        raise

# ====== Coinbase API Request ======
def cb_request(method, endpoint, params=None, body=None):
    """Perform authenticated request to Coinbase API"""
    url = f"{CB_BASE_URL}{endpoint}"

    # Build JWT for this request
    request_path = endpoint
    if params:
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        request_path = f"{endpoint}?{query}"

    jwt_token = build_jwt(method.upper(), request_path)

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
    }

    log(f"📡 {method.upper()} {url}")

    if method.upper() == "GET":
        res = requests.get(url, headers=headers, params=params)
    else:
        res = requests.post(url, headers=headers, data=json.dumps(body or {}))

    log(f"📥 Response: {res.status_code}")
    if res.status_code >= 400:
        log(f"❌ Error body: {res.text}")
        raise Exception(f"Coinbase error {res.status_code}: {res.text}")

    return res.json()

# ====== Helper functions ======
def get_balances():
    """Retrieve Coinbase balances"""
    balances = {}
    try:
        res = cb_request("GET", ENDPOINT_ACCOUNTS)
        for account in res.get("accounts", []):
            currency = account.get("currency")
            available = float(account.get("available_balance", {}).get("value", 0))
            if available > 0:
                balances[currency] = balances.get(currency, 0.0) + available
        return balances
    except Exception as e:
        log(f"Error getting balances: {e}")
        return {}

# ====== ROUTES ======
@app.route("/", methods=["GET"])
def home():
    """Home page"""
    try:
        balances = get_balances()
        balance_html = "<br>".join([f"<strong>{k}:</strong> {v}" for k, v in balances.items()]) or "No balances found."
        return f"""
        <html style="font-family:Arial">
        <h1>TradingView → Coinbase Bridge ✅</h1>
        <p>Status: Online</p>
        <div style="background:#e8f5e9;padding:20px;border-radius:10px">
            <h3>💰 Balances</h3>{balance_html}
        </div>
        <p>Endpoints:</p>
        <ul>
            <li>/health → Balance check</li>
            <li>/webhook → POST from TradingView</li>
        </ul>
        </html>
        """
    except Exception as e:
        return f"<h1>Error loading balances</h1><p>{str(e)}</p>", 500

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    try:
        balances = get_balances()
        return jsonify({"status": "ok", "balances": balances}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/webhook", methods=["POST"])
def webhook():
    """Webhook listener from TradingView"""
    try:
        data_raw = request.get_data(as_text=True)
        log(f"📨 Received alert: {data_raw}")

        # Example: "symbol: BTC-USD; action: buy"
        parsed = {}
        for part in data_raw.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                parsed[k.strip().lower()] = v.strip()

        symbol = parsed.get("symbol", "BTC-USD")
        action = parsed.get("action", "buy").lower()

        log(f"✅ Parsed alert for {symbol}, action={action}")

        # Here you could place orders if desired
        return jsonify({"status": "received", "symbol": symbol, "action": action}), 200

    except Exception as e:
        log(f"❌ Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

# ====== RUN (only needed for local or Render gunicorn) ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log(f"🚀 Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)
