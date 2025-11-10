import os
import time
import json
import traceback
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

# Store logs in memory for display
log_buffer = []

def log(msg):
    """Simple timestamped console logger"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{timestamp}] {msg}"
    print(log_msg)
    log_buffer.append(log_msg)
    # Keep only last 100 logs
    if len(log_buffer) > 100:
        log_buffer.pop(0)

# ====== JWT Builder ======
def build_jwt(request_method, request_path):
    """Creates a JWT token for Coinbase Advanced Trade API"""
    try:
        private_key_str = CB_PRIVATE_KEY

        if not private_key_str:
            raise Exception("CB_PRIVATE_KEY environment variable is empty or not set")

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

        log("✅ JWT token generated successfully")
        return token
    except Exception as e:
        log(f"❌ JWT generation failed: {e}")
        log(f"Private key length: {len(CB_PRIVATE_KEY)}")
        log(f"Private key starts with: {CB_PRIVATE_KEY[:30] if CB_PRIVATE_KEY else 'EMPTY'}")
        log(f"API Key Name: {CB_API_KEY_NAME[:15] if CB_API_KEY_NAME else 'EMPTY'}...")
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

    try:
        if method.upper() == "GET":
            res = requests.get(url, headers=headers, params=params, timeout=10)
        else:
            res = requests.post(url, headers=headers, data=json.dumps(body or {}), timeout=10)

        log(f"📥 Response Status: {res.status_code}")
        log(f"📥 Response Headers: {dict(res.headers)}")
        log(f"📥 Response Body: {res.text[:500]}")
        
        if res.status_code >= 400:
            log(f"❌ Error body: {res.text}")
            raise Exception(f"Coinbase API error {res.status_code}: {res.text}")

        return res.json()
    except requests.exceptions.RequestException as e:
        log(f"❌ Request failed: {e}")
        raise

# ====== Helper functions ======
def get_balances():
    """Retrieve Coinbase balances with enhanced debugging"""
    balances = {}
    try:
        log("🔍 Fetching balances from Coinbase...")
        res = cb_request("GET", ENDPOINT_ACCOUNTS)
        
        # Log the full response to see what you're actually getting
        log(f"📊 Full API Response: {json.dumps(res, indent=2)}")
        
        # Check if accounts key exists
        if "accounts" not in res:
            log(f"⚠️ No 'accounts' key in response. Keys found: {list(res.keys())}")
            return balances
        
        accounts = res.get("accounts", [])
        log(f"📈 Found {len(accounts)} total accounts")
        
        if len(accounts) == 0:
            log("⚠️ No accounts returned from API")
            return balances
        
        for idx, account in enumerate(accounts):
            log(f"\n--- Account {idx + 1} ---")
            log(f"Account data: {json.dumps(account, indent=2)}")
            
            currency = account.get("currency", "UNKNOWN")
            
            # Try different possible balance field names
            available = 0.0
            balance_source = "none"
            
            if "available_balance" in account:
                available = float(account["available_balance"].get("value", 0))
                balance_source = "available_balance"
            elif "balance" in account:
                available = float(account["balance"].get("value", 0))
                balance_source = "balance"
            elif "available" in account:
                available = float(account.get("available", 0))
                balance_source = "available"
            
            log(f"💵 {currency}: {available} (from '{balance_source}')")
            
            if available > 0:
                balances[currency] = balances.get(currency, 0.0) + available
        
        log(f"\n✅ Final balances: {balances}")
        return balances
        
    except Exception as e:
        log(f"❌ Error getting balances: {e}")
        log(f"Full traceback:\n{traceback.format_exc()}")
        return {}

def test_api_connection():
    """Test API connection and permissions"""
    log("\n🧪 Testing API Connection...")
    results = {
        "env_vars_set": False,
        "jwt_generation": False,
        "api_connection": False,
        "balances_found": False,
        "error": None
    }
    
    try:
        # Test 1: Check environment variables
        if CB_API_KEY_NAME and CB_PRIVATE_KEY:
            log(f"✓ API Key Name: {CB_API_KEY_NAME[:15]}...")
            log(f"✓ Private Key Length: {len(CB_PRIVATE_KEY)} chars")
            results["env_vars_set"] = True
        else:
            log("❌ Missing API credentials in environment variables!")
            results["error"] = "Missing CB_API_KEY_NAME or CB_PRIVATE_KEY environment variables"
            return results
        
        # Test 2: Try JWT generation
        try:
            test_jwt = build_jwt("GET", ENDPOINT_ACCOUNTS)
            results["jwt_generation"] = True
            log("✅ JWT generation successful")
        except Exception as e:
            results["error"] = f"JWT generation failed: {str(e)}"
            log(f"❌ JWT generation failed: {e}")
            return results
        
        # Test 3: Try to get accounts
        balances = get_balances()
        results["api_connection"] = True
        
        if balances:
            log(f"✅ API Connection Successful! Found balances: {balances}")
            results["balances_found"] = True
        else:
            log("⚠️ API connected but no balances found")
            results["error"] = "API connected successfully but no balances returned"
            
        return results
            
    except Exception as e:
        log(f"❌ API Connection Test Failed: {e}")
        results["error"] = str(e)
        return results

# ====== ROUTES ======
@app.route("/", methods=["GET"])
def home():
    """Home page with balance display"""
    try:
        balances = get_balances()
        
        if balances:
            balance_html = "<br>".join([f"<strong>{k}:</strong> {v}" for k, v in balances.items()])
            status_color = "green"
            status_msg = "✅ Balances loaded successfully"
        else:
            balance_html = "<span style='color:orange'>⚠️ No balances found or API error. Check logs below or visit /test</span>"
            status_color = "orange"
            status_msg = "⚠️ No balances found"
        
        # Get last 20 logs
        recent_logs = log_buffer[-20:] if log_buffer else ["No logs yet"]
        logs_html = "<br>".join([f"<code>{log}</code>" for log in recent_logs])
        
        return f"""
        <html style="font-family:Arial">
        <head>
            <title>TradingView → Coinbase Bridge</title>
            <style>
                body {{ padding: 20px; background: #f5f5f5; }}
                .card {{ background: white; padding: 20px; border-radius: 10px; margin: 10px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                .success {{ background: #e8f5e9; }}
                .info {{ background: #e3f2fd; }}
                .warning {{ background: #fff3e0; }}
                .logs {{ background: #263238; color: #aed581; padding: 15px; border-radius: 5px; font-family: monospace; font-size: 12px; max-height: 400px; overflow-y: auto; }}
                code {{ background: transparent; color: #aed581; }}
            </style>
        </head>
        <body>
            <h1>🤖 TradingView → Coinbase Bridge</h1>
            
            <div class="card" style="background: {status_color}20; border-left: 4px solid {status_color}">
                <h3 style="color: {status_color}">{status_msg}</h3>
            </div>
            
            <div class="card success">
                <h3>💰 Account Balances</h3>
                {balance_html}
            </div>
            
            <div class="card info">
                <h3>📡 API Endpoints</h3>
                <ul>
                    <li><strong>/</strong> → This page (balance display)</li>
                    <li><strong>/health</strong> → JSON health check</li>
                    <li><strong>/webhook</strong> → POST endpoint for TradingView alerts</li>
                    <li><strong>/test</strong> → Test API connection (JSON)</li>
                    <li><strong>/logs</strong> → View all logs</li>
                </ul>
            </div>
            
            <div class="card warning">
                <h3>📋 Recent Logs (Last 20)</h3>
                <div class="logs">
                    {logs_html}
                </div>
                <p><a href="/logs">View all logs</a></p>
            </div>
            
            <div class="card">
                <p><strong>Status:</strong> <span style="color:green">✅ Online</span></p>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>API Key Set:</strong> {'✅ Yes' if CB_API_KEY_NAME else '❌ No'}</p>
                <p><strong>Private Key Set:</strong> {'✅ Yes' if CB_PRIVATE_KEY else '❌ No'}</p>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        log(f"❌ Error in home route: {e}")
        log(f"Traceback:\n{traceback.format_exc()}")
        return f"""
        <html style="font-family:Arial">
        <h1>❌ Error Loading Page</h1>
        <div style="background:#ffebee;padding:20px;border-radius:10px">
            <p><strong>Error:</strong> {str(e)}</p>
            <pre>{traceback.format_exc()}</pre>
        </div>
        </html>
        """, 500

@app.route("/logs", methods=["GET"])
def logs():
    """Display all logs"""
    logs_html = "<br>".join([f"<code>{log}</code>" for log in log_buffer]) if log_buffer else "No logs yet"
    return f"""
    <html style="font-family:Arial">
    <head>
        <title>Logs - Coinbase Bridge</title>
        <style>
            body {{ padding: 20px; background: #f5f5f5; }}
            .logs {{ background: #263238; color: #aed581; padding: 20px; border-radius: 10px; font-family: monospace; font-size: 12px; }}
            code {{ background: transparent; color: #aed581; }}
        </style>
    </head>
    <body>
        <h1>📋 All Logs</h1>
        <p><a href="/">← Back to home</a></p>
        <div class="logs">
            {logs_html}
        </div>
    </body>
    </html>
    """

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint with detailed status"""
    try:
        balances = get_balances()
        return jsonify({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "balances": balances,
            "api_configured": bool(CB_API_KEY_NAME and CB_PRIVATE_KEY),
            "recent_logs": log_buffer[-10:]
        }), 200
    except Exception as e:
        log(f"❌ Health check failed: {e}")
        return jsonify({
            "status": "error",
            "timestamp": datetime.now().isoformat(),
            "message": str(e),
            "traceback": traceback.format_exc(),
            "api_configured": bool(CB_API_KEY_NAME and CB_PRIVATE_KEY),
            "recent_logs": log_buffer[-10:]
        }), 500

@app.route("/test", methods=["GET"])
def test():
    """Test API connection endpoint"""
    results = test_api_connection()
    status_code = 200 if results["api_connection"] else 500
    return jsonify({
        "results": results,
        "logs": log_buffer[-20:]
    }), status_code

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
        return jsonify({
            "status": "received",
            "symbol": symbol,
            "action": action,
            "timestamp": datetime.now().isoformat()
        }), 200

    except Exception as e:
        log(f"❌ Webhook error: {e}")
        log(f"Traceback:\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": str(e)}), 400

# ====== STARTUP ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    log(f"🚀 Starting Flask app on port {port}")
    log(f"📍 Base URL: {CB_BASE_URL}")
    log(f"🔑 API Key Name: {CB_API_KEY_NAME[:15] if CB_API_KEY_NAME else 'NOT SET'}...")
    log(f"🔐 Private Key: {'SET (' + str(len(CB_PRIVATE_KEY)) + ' chars)' if CB_PRIVATE_KEY else 'NOT SET'}")
    
    # Run connection test on startup
    test_api_connection()
    
    app.run(host="0.0.0.0", port=port, debug=False)
