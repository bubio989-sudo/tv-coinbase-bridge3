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

ENDPOINT_ACCOUNTS = "/api/v3/brokerage/accounts"
ENDPOINT_PRODUCTS = "/api/v3/brokerage/products"
ENDPOINT_TICKER = "/api/v3/brokerage/products/{product_id}/ticker"
ENDPOINT_ORDER = "/api/v3/brokerage/orders"

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
def build_jwt(request_method, request_path):
    """Build JWT token for Coinbase Advanced Trade API"""
    try:
        # Handle both formats: with real newlines or \n escape sequences
        private_key_str = CB_PRIVATE_KEY
        
        # If it's a single line with \n, replace with actual newlines
        if '\\n' in private_key_str:
            private_key_str = private_key_str.replace('\\n', '\n')
        
        # Remove any extra quotes that might have been added
        private_key_str = private_key_str.strip().strip('"').strip("'")
        
        # Ensure it starts and ends correctly
        if not private_key_str.startswith('-----BEGIN'):
            raise Exception("Private key must start with -----BEGIN EC PRIVATE KEY-----")
        if not private_key_str.endswith('-----'):
            raise Exception("Private key must end with -----END EC PRIVATE KEY-----")
        
        log(f"🔐 Private key format check: starts={private_key_str[:30]}, ends={private_key_str[-30:]}")
        
        # Load private key
        private_key = serialization.load_pem_private_key(
            private_key_str.encode('utf-8'),
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
        log(f"Private key length: {len(CB_PRIVATE_KEY)}")
        log(f"Private key first 50 chars: {CB_PRIVATE_KEY[:50]}")
        raise
