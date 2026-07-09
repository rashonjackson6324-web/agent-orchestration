#!/usr/bin/env python3
"""
TASK WATCHDOG
SQLite task board + Telegram commands + URL health checks + GCP SSH ping.
GCP host: ~/agents/task_watchdog/task_watchdog.py
Fires: 8AM ET daily + every 6h watchdog
"""

import os, json, sqlite3, logging, subprocess, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH          = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/task_watchdog.db")
LOG_PATH         = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/logs/task_watchdog.log")
GCP_IP           = os.getenv("GCP_IP")
GCP_USER         = os.getenv("GCP_USER")
ET               = ZoneInfo("America/New_York")

MONITORED_URLS = [
    ("primary_app",        os.getenv("APP_URL", "")),
    ("secondary_app",    os.getenv("SECONDARY_APP_URL", "")),
    ("n8n",              f"http://{GCP_IP}:5678"),
    ("AnythingLLM",      "http://localhost:3001"),
]

MONITORED_PIDS = ["trading_executor", "signal_parser", "domain_scorer", "task_watchdog", "output_validator", "strategy_agent"]

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
log = logging.getLogger("task_watchdog")

# ── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            priority    TEXT DEFAULT 'medium',
            due_date    TEXT,
            status      TEXT DEFAULT 'open',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            source      TEXT DEFAULT 'manual',
            notes       TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_task(name, priority="medium", due_date=None, source="telegram", notes=""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO tasks (name, priority, due_date, source, notes) VALUES (?, ?, ?, ?, ?)",
        (name, priority, due_date, source, notes)
    )
    conn.commit()
    conn.close()

def complete_task(task_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE tasks SET status='done', completed_at=? WHERE id=?",
        (datetime.now().isoformat(), task_id)
    )
    conn.commit()
    conn.close()

def get_tasks(filter_type="open"):
    conn = sqlite3.connect(DB_PATH)
    if filter_type == "open":
        rows = conn.execute(
            "SELECT id, name, priority, due_date FROM tasks WHERE status='open' ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
        ).fetchall()
    elif filter_type == "today":
        today = date.today().isoformat()
        rows = conn.execute(
            "SELECT id, name, priority, due_date FROM tasks WHERE status='open' AND (due_date=? OR due_date IS NULL) LIMIT 5",
            (today,)
        ).fetchall()
    elif filter_type == "overdue":
        today = date.today().isoformat()
        rows = conn.execute(
            "SELECT id, name, priority, due_date FROM tasks WHERE status='open' AND due_date < ?",
            (today,)
        ).fetchall()
    elif filter_type == "week":
        end = (date.today() + timedelta(days=7)).isoformat()
        rows = conn.execute(
            "SELECT id, name, priority, due_date FROM tasks WHERE status='open' AND due_date <= ?",
            (end,)
        ).fetchall()
    else:
        rows = []
    conn.close()
    return rows

# ── URL HEALTH ────────────────────────────────────────────────────────────────
def check_urls():
    results = []
    for name, url in MONITORED_URLS:
        try:
            r = requests.get(url, timeout=8)
            status = "✅" if r.status_code < 400 else "⚠️"
            results.append(f"{status} {name}: HTTP {r.status_code}")
        except Exception as e:
            results.append(f"❌ {name}: DOWN ({str(e)[:40]})")
    return results

# ── SYSTEMD CHECKS ────────────────────────────────────────────────────────────
def check_services():
    results = []
    for svc in MONITORED_PIDS:
        try:
            r = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5
            )
            status = "✅" if r.stdout.strip() == "active" else "❌"
            results.append(f"{status} {svc}: {r.stdout.strip()}")
        except Exception as e:
            results.append(f"⚠️ {svc}: check failed")
    return results

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def telegram(msg):
    if not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]},
            timeout=8
        )
    except Exception as e:
        log.warning("Telegram failed: %s", e)

def priority_icon(p):
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "⬜")

def task_list_msg(tasks, header):
    if not tasks:
        return f"{header}\n  (none)"
    lines = [header]
    for t in tasks:
        tid, name, priority, due = t
        due_str = f" | due: {due}" if due else ""
        lines.append(f"  {priority_icon(priority)} [{tid}] {name}{due_str}")
    return "\n".join(lines)

# ── DAILY DIGEST ──────────────────────────────────────────────────────────────
def daily_digest():
    date_str  = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    today_t   = get_tasks("today")
    overdue_t = get_tasks("overdue")
    open_t    = get_tasks("open")
    url_check = check_urls()
    svc_check = check_services()

    lines = [
        "═══════════════════════════════════════",
        f"⚓ WATCHDOG DAILY DIGEST — {date_str}",
        "═══════════════════════════════════════",
        f"Open: {len(open_t)} | Overdue: {len(overdue_t)}",
        "",
    ]

    if overdue_t:
        lines.append(task_list_msg(overdue_t, "🚨 OVERDUE:"))
        lines.append("")

    lines.append(task_list_msg(today_t or open_t[:5], "📋 TODAY / PRIORITY:"))
    lines += [
        "",
        "── URL HEALTH ──────────────────────────",
    ] + url_check + [
        "",
        "── SERVICES ────────────────────────────",
    ] + svc_check[:6] + [
        "",
        "── COMMANDS ────────────────────────────",
        "/tasks | /add <task> | /done <id> | /overdue | /today | /week",
        "═══════════════════════════════════════"
    ]

    msg = "\n".join(lines)
    log.info(msg)
    telegram(msg)

    # Save state for strategy_agent
    state = {
        "ts": datetime.now(ET).isoformat(),
        "open_tasks": len(open_t),
        "overdue_tasks": len(overdue_t),
        "url_health": url_check,
        "services": svc_check
    }
    out = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/task_watchdog_state.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(state, f, indent=2)

# ── WATCHDOG ──────────────────────────────────────────────────────────────────
def watchdog():
    """Every-6h silent check — only alerts on failure."""
    failures = []
    for name, url in MONITORED_URLS:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code >= 400:
                failures.append(f"❌ {name}: HTTP {r.status_code}")
        except Exception as e:
            failures.append(f"❌ {name}: DOWN")

    for svc in ["trading_executor", "signal_parser"]:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            if r.stdout.strip() != "active":
                failures.append(f"❌ {svc}: not active")
        except Exception:
            failures.append(f"❌ {svc}: check failed")

    if failures:
        msg = "🚨 WATCHDOG ALERT\n" + "\n".join(failures)
        log.warning(msg)
        telegram(msg)
    else:
        log.info("WATCHDOG: all systems nominal")

# ── ENTRY ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "digest"
    init_db()

    if mode == "digest":
        daily_digest()
    elif mode == "watchdog":
        watchdog()
    elif mode == "add" and len(sys.argv) > 2:
        name = " ".join(sys.argv[2:])
        add_task(name, source="cli")
        log.info("Task added: %s", name)
    elif mode == "done" and len(sys.argv) > 2:
        complete_task(int(sys.argv[2]))
        log.info("Task %s completed", sys.argv[2])
    else:
        daily_digest()
