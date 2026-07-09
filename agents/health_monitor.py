#!/usr/bin/env python3
"""
HEALTH MONITOR — Silent Success, Loud Failure
Rule: 2 consecutive failures = alert. Recovery = alert. All clear = silence.
GCP host: ~/.agent-network/health_monitor.py
Fires: every 20 minutes via systemd timer
"""

import os, json, subprocess, requests
from datetime import datetime
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE       = "/tmp/agent_health_state.json"
THRESHOLD        = 2
ET               = ZoneInfo("America/New_York")

SYSTEMD_SERVICES = ["trading_executor", "domain_scorer", "task_watchdog", "output_validator", "strategy_agent",
                    "repo_scanner", "health_monitor"]
HTTP_CHECKS      = [
    ("n8n",       "http://localhost:5678"),
    ("csv_bridge","http://localhost:8765/jobs/pending"),
]

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)

def check_systemd(name):
    r = subprocess.run(["systemctl", "is-active", name],
                       capture_output=True, text=True, timeout=5)
    return r.stdout.strip() == "active"

def check_http(url):
    try:
        r = requests.get(url, timeout=6)
        return r.status_code < 500
    except Exception:
        return False

def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=8
        )
    except Exception:
        pass

def main():
    state     = load_state()
    failures  = []
    recoveries = []
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M")

    for svc in SYSTEMD_SERVICES:
        healthy = check_systemd(svc)
        prev    = state.get(svc, 0)
        if not healthy:
            state[svc] = prev + 1
            if state[svc] >= THRESHOLD:
                failures.append(f"❌ {svc.upper()} — DOWN ({state[svc]} checks failed)")
        else:
            if prev >= THRESHOLD:
                recoveries.append(f"✅ {svc.upper()} — RECOVERED")
            state[svc] = 0

    for name, url in HTTP_CHECKS:
        healthy   = check_http(url)
        key       = f"http_{name}"
        prev      = state.get(key, 0)
        if not healthy:
            state[key] = prev + 1
            if state[key] >= THRESHOLD:
                failures.append(f"❌ {name.upper()} — DOWN ({state[key]} checks failed)")
        else:
            if prev >= THRESHOLD:
                recoveries.append(f"✅ {name.upper()} — RECOVERED")
            state[key] = 0

    save_state(state)

    if failures:
        send_telegram(f"🚨 ALERT — {ts}\n\n" + "\n".join(failures) + "\n\nCheck host.")
    elif recoveries:
        send_telegram(f"🟢 RECOVERY — {ts}\n\n" + "\n".join(recoveries))
    # All clear = silence

if __name__ == "__main__":
    main()
