#!/usr/bin/env python3
"""
INTEL AGGREGATOR
Reads ALL intel digests (GitHub, YouTube, Twitter).
Extracts actions. Queues to action_queue. Deduplicates.
GCP host: ~/.agent-network/intel_aggregator.py
Fires: after each digest agent + manual --run
"""
import os, json, sqlite3, logging, glob, requests
from datetime import datetime
from zoneinfo import ZoneInfo

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH           = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/intel_aggregator.db")
INTEL_DIR         = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/intel")
LOG_PATH          = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/logs/intel_aggregator.log")
TASK_DB           = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/task_watchdog.db")
ET                = ZoneInfo("America/New_York")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INTEL] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
log = logging.getLogger("intel_aggregator")

SYSTEM_PROMPT = """
You are intel_aggregator, the action extractor for the agent network.
Read the intel digest and extract ONLY concrete actions the agent network should take.
No fluff. No maybes. Real actions only.

For each action, output JSON:
{"actions": [
  {"priority": "high|medium|low", "action": "exact thing to do", "agent": "action_queue|executor|manual", "type": "integrate|research|content|outreach"}
]}

Integrate = clone/install a repo
Research = investigate further
Content = generate content for the publishing queue
Outreach = contact someone

Max 5 actions per digest. If nothing actionable, return {"actions": []}
"""

# ── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_digests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            filename   TEXT UNIQUE,
            ts         TEXT,
            actions_found INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queued_actions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT,
            source     TEXT,
            priority   TEXT,
            action     TEXT UNIQUE,
            agent      TEXT,
            type       TEXT,
            status     TEXT DEFAULT 'queued'
        )
    """)
    conn.commit()
    conn.close()

def is_processed(filename):
    conn = sqlite3.connect(DB_PATH)
    r = conn.execute("SELECT id FROM processed_digests WHERE filename=?", (filename,)).fetchone()
    conn.close()
    return r is not None

def mark_processed(filename, action_count):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO processed_digests (filename, ts, actions_found) VALUES (?,?,?)",
        (filename, datetime.now(ET).isoformat(), action_count)
    )
    conn.commit()
    conn.close()

def save_action(source, priority, action, agent, action_type):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO queued_actions (ts, source, priority, action, agent, type) VALUES (?,?,?,?,?,?)",
            (datetime.now(ET).isoformat(), source, priority, action, agent, action_type)
        )
        conn.commit()
        return True
    except Exception as e:
        log.debug("Dedup skip: %s", e)
        return False
    finally:
        conn.close()

def queue_to_task_db(action, assigned_to="action_queue"):
    """Queue action to task_watchdog's task DB for Auto Executor."""
    try:
        conn = sqlite3.connect(TASK_DB)
        conn.execute(
            "INSERT INTO tasks(name, type, command, priority, assigned_to) VALUES(?,?,?,?,?)",
            (action[:100], "generate", action, 1, assigned_to)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        log.warning("Task queue failed: %s", e)
        return False

# ── CLAUDE EXTRACTION ─────────────────────────────────────────────────────────
def extract_actions(digest_text, source_name):
    if not ANTHROPIC_API_KEY:
        return []
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": digest_text[:3000]}]
            },
            timeout=20
        )
        content = r.json()["content"][0]["text"].strip()
        content = content.replace("```json", "").replace("```", "").strip()
        data = json.loads(content)
        return data.get("actions", [])
    except Exception as e:
        log.warning("Extract failed for %s: %s", source_name, e)
        return []

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
        log.warning("Telegram: %s", e)

# ── MAIN RUN ──────────────────────────────────────────────────────────────────
def run():
    log.info("intel_aggregator — PATROL START")
    init_db()

    # Find all digest files
    patterns = [
        os.path.join(INTEL_DIR, "github-digest-*.md"),
        os.path.join(INTEL_DIR, "youtube-*.md"),
        os.path.join(INTEL_DIR, "twitter-*.md"),
        os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/repo_scanner_latest.json"),
    ]

    all_files = []
    for pattern in patterns:
        all_files.extend(glob.glob(pattern))

    new_files    = [f for f in all_files if not is_processed(os.path.basename(f))]
    total_queued = 0

    if not new_files:
        log.info("No new digests to process")
        return

    log.info("New digests: %d", len(new_files))
    report_lines = [
        f"🕵️ intel_aggregator REPORT — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}",
        f"Digests processed: {len(new_files)}",
        "───────────────────────────────────────"
    ]

    for filepath in new_files:
        fname    = os.path.basename(filepath)
        src_name = fname.split("-")[0].upper()

        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            log.warning("Could not read %s: %s", fname, e)
            continue

        actions = extract_actions(content, fname)
        queued  = 0

        for a in actions:
            action_text = a.get("action", "").strip()
            priority    = a.get("priority", "medium")
            agent       = a.get("agent", "action_queue")
            atype       = a.get("type", "research")

            if not action_text:
                continue

            if save_action(fname, priority, action_text, agent, atype):
                if agent != "manual":
                    queue_to_task_db(action_text, agent)
                queued += 1
                total_queued += 1
                p_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "⬜")
                report_lines.append(f"{p_icon} [{src_name}] {action_text[:80]}")

        mark_processed(fname, queued)
        log.info("Processed %s: %d actions queued", fname, queued)

    report_lines += [
        "───────────────────────────────────────",
        f"Total actions queued: {total_queued}",
        "intel_aggregator Status: WATCHING 👁️"
    ]

    report = "\n".join(report_lines)
    log.info("\n%s", report)

    if total_queued > 0:
        telegram(report)
    else:
        log.info("No new actions — silent run")

def status():
    init_db()
    conn    = sqlite3.connect(DB_PATH)
    pending = conn.execute("SELECT COUNT(*) FROM queued_actions WHERE status='queued'").fetchone()[0]
    done    = conn.execute("SELECT COUNT(*) FROM queued_actions WHERE status='done'").fetchone()[0]
    digests = conn.execute("SELECT COUNT(*) FROM processed_digests").fetchone()[0]
    conn.close()
    msg = f"INTEL STATUS: {pending} pending | {done} done | {digests} digests processed"
    log.info(msg)
    print(msg)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        status()
    else:
        run()
