#!/usr/bin/env python3
"""
OPS ORCHESTRATOR — proactive trigger layer.

Watches for conditions across the agent network and queues tasks into the
executor without waiting to be asked. This is the difference between a manual
operation and a self-running one.

Watches:
  - Agent health (fed by the verifier)
  - File drops in watched folders
  - Threshold breaches
  - Time-based triggers beyond simple cron
"""

import os,sys,time,sqlite3,json,requests,datetime,argparse,logging,hashlib
import pathlib
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/.env"),override=True)

ANTHROPIC_KEY=os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT=os.getenv("TELEGRAM_CHAT_ID")
EXECUTOR_DB=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/task_watchdog.db")
OPS_DB=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/ops_orchestrator.db")
LOG=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/ops_orchestrator.log")
POLL=300  # Check every 5 minutes

logging.basicConfig(level=20,format="%(asctime)s [OPS] %(message)s",
    handlers=[logging.StreamHandler(),logging.FileHandler(LOG)])
log=logging.getLogger(__name__)

# Client-brief prompt is business content, not code. Gitignored.
BRIEF_PATH = pathlib.Path(os.getenv("CLIENT_BRIEF_TEMPLATE", "config/client_brief_prompt.md"))
CLIENT_BRIEF_TEMPLATE = (
    BRIEF_PATH.read_text(encoding="utf-8") if BRIEF_PATH.exists()
    else "Generate a client intelligence brief for this prospect:\n{content}\n\n"
         "Include: company summary, pain points, automation opportunities, "
         "discovery questions, pricing recommendation, risk flags."
)

# Outreach copy is business content, not code. Gitignored.
OUTREACH_PATH = pathlib.Path(os.getenv("OUTREACH_TEMPLATE", "config/outreach_prompt.md"))
OUTREACH_TEMPLATE = (
    OUTREACH_PATH.read_text(encoding="utf-8") if OUTREACH_PATH.exists()
    else "Generate 5 cold outreach messages for your service, targeting your "
         "chosen segment. Include: subject line, 3-sentence pitch, clear CTA."
)

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg(m):
    log.info("[TG] "+m[:120])
    try:
        requests.post(
            "https://api.telegram.org/bot"+TELEGRAM_TOKEN+"/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":m},timeout=10)
    except:pass

# ── CLAUDE HAIKU ──────────────────────────────────────────────────────────────
def haiku(prompt,max_tokens=200):
    try:
        import anthropic
        c=anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg=c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role":"user","content":prompt}])
        return msg.content[0].text.strip()
    except Exception as e:
        log.error("Haiku: "+str(e))
        return None

# ── OPS DB ───────────────────────────────────────────────────────────────────
def init_db():
    c=sqlite3.connect(OPS_DB)
    # Track what ops_orchestrator has already acted on (prevent duplicate queuing)
    c.execute("""CREATE TABLE IF NOT EXISTS ops_actions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_type TEXT,
        trigger_key TEXT UNIQUE,
        action_taken TEXT,
        task_id INTEGER,
        created_at TEXT DEFAULT(datetime('now')))""")
    # Watch state (file hashes, last check times etc)
    c.execute("""CREATE TABLE IF NOT EXISTS watch_state(
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT(datetime('now')))""")
    c.commit()
    c.close()

def already_acted(trigger_key):
    """Check if ops_orchestrator already acted on this trigger today."""
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    key=f"{trigger_key}:{today}"
    c=sqlite3.connect(OPS_DB)
    row=c.execute("SELECT id FROM ops_actions WHERE trigger_key=?",(key,)).fetchone()
    c.close()
    return row is not None

def record_action(trigger_type,trigger_key,action_taken,task_id=None):
    """Record that ops_orchestrator took an action."""
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    key=f"{trigger_key}:{today}"
    try:
        c=sqlite3.connect(OPS_DB)
        c.execute(
            "INSERT OR IGNORE INTO ops_actions(trigger_type,trigger_key,action_taken,task_id) VALUES(?,?,?,?)",
            (trigger_type,key,action_taken,task_id))
        c.commit()
        c.close()
    except Exception as e:
        log.error("Record action: "+str(e))

def get_state(key,default=None):
    c=sqlite3.connect(OPS_DB)
    row=c.execute("SELECT value FROM watch_state WHERE key=?",(key,)).fetchone()
    c.close()
    return row[0] if row else default

def set_state(key,value):
    c=sqlite3.connect(OPS_DB)
    c.execute("INSERT OR REPLACE INTO watch_state(key,value,updated_at) VALUES(?,?,?)",
              (key,str(value),datetime.datetime.now().isoformat()))
    c.commit()
    c.close()

# ── EXECUTOR QUEUE ────────────────────────────────────────────────────────────
def queue_task(name,task_type,command,priority=3,assigned_to="executor"):
    """Queue a task into the Auto Executor."""
    try:
        c=sqlite3.connect(EXECUTOR_DB)
        c.execute(
            "INSERT INTO tasks(name,type,command,priority,assigned_to) VALUES(?,?,?,?,?)",
            (name,task_type,command,priority,assigned_to))
        tid=c.lastrowid
        c.commit()
        c.close()
        log.info(f"ops_orchestrator queued task #{tid}: {name}")
        return tid
    except Exception as e:
        log.error("Queue task: "+str(e))
        return None

# ── WATCH CONDITIONS ──────────────────────────────────────────────────────────

def watch_trading_executor_activity():
    """If trading_executor has no log activity in 24h — queue diagnostic."""
    trading_executor_log=os.path.expanduser("~/trade-signal-bot/trading_executor.log")
    if not Path(trading_executor_log).exists():
        # Try alternate log locations
        for p in ["/var/log/trading_executor.log",os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/trading_executor.log")]:
            if Path(p).exists():
                trading_executor_log=p
                break
        else:
            return
    stat=Path(trading_executor_log).stat()
    age_hours=(time.time()-stat.st_mtime)/3600
    if age_hours>24 and not already_acted("trading_executor_silent"):
        log.info(f"trading_executor log silent for {age_hours:.1f}h — queuing diagnostic")
        tid=queue_task(
            "trading_executor diagnostic — no log activity 24h",
            "shell",
            "systemctl --no-pager status trading_executor.service",
            priority=1)
        record_action("trading_executor_silent","trading_executor_silent",f"Queued diagnostic #{tid}",tid)
        tg(f"ops_orchestrator queued trading_executor diagnostic — no log activity in {age_hours:.0f}h")


def watch_consulting_inbox():
    """If new file in consulting new-client folder — queue client brief."""
    inbox=os.path.expanduser(os.getenv("CLIENT_INBOX", "~/data/new-client"))
    Path(inbox).mkdir(parents=True,exist_ok=True)
    files=list(Path(inbox).glob("*.txt"))+list(Path(inbox).glob("*.md"))
    for f in files:
        file_key=f"consulting_{f.name}"
        if not already_acted(file_key):
            content=f.read_text()[:500]
            log.info(f"New consulting client file: {f.name}")
            tid=queue_task(
                f"client brief — {f.name}",
                "generate",
                CLIENT_BRIEF_TEMPLATE.format(content=content),
                priority=2,
                assigned_to="action_queue")
            record_action("consulting_new_client",file_key,f"Queued brief #{tid}",tid)
            tg(f"ops_orchestrator queued consulting brief for: {f.name}")


def watch_github_digest():
    """If new GitHub scanner digest exists — queue review notification."""
    digest_dir=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/GitHub-Intel")
    if not Path(digest_dir).exists():
        return
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    digest_file=Path(digest_dir)/f"github-digest-{today}.md"
    if digest_file.exists() and not already_acted(f"github_digest_{today}"):
        log.info("New GitHub digest found")
        content=digest_file.read_text()[:500]
        record_action("github_digest",f"github_digest_{today}","Notified",None)
        tg(f"GitHub Intel digest ready for {today}. Check ~/.agent-network/GitHub-Intel/")

def watch_domain_scorer_score():
    """Check domain_scorer's last report for low composite score."""
    domain_scorer_log="/var/log/domain_scorer.log"
    if not Path(domain_scorer_log).exists():
        return
    try:
        content=Path(domain_scorer_log).read_text()
        # Look for COMPOSITE: XX% pattern
        import re
        matches=re.findall(r'COMPOSITE[:\s]+(\d+)%',content)
        if matches:
            last_score=int(matches[-1])
            if last_score<50 and not already_acted(f"domain_scorer_low_{last_score}"):
                log.info(f"domain_scorer composite low: {last_score}%")
                tid=queue_task(
                    f"domain_scorer low score investigation — {last_score}%",
                    "shell",
                    "systemctl --no-pager status 'domain_scorer.service'",
                    priority=2)
                record_action("domain_scorer_low",f"domain_scorer_low_{last_score}",f"Queued investigation #{tid}",tid)
                tg(f"ops_orchestrator: domain_scorer composite at {last_score}%. Queued system check.")
    except Exception as e:
        log.error("domain_scorer watch: "+str(e))

def watch_task_failures():
    """If tasks in executor DB are repeatedly failing — alert."""
    try:
        c=sqlite3.connect(EXECUTOR_DB)
        rows=c.execute(
            "SELECT name,COUNT(*) as fails FROM tasks "
            "WHERE status='failed' AND updated_at>=datetime('now','-1 day') "
            "GROUP BY name HAVING fails>=2").fetchall()
        c.close()
        for name,fails in rows:
            key=f"task_fail_{name}"
            if not already_acted(key):
                record_action("task_failure",key,f"Alerted on {fails} failures",None)
                tg(f"ops_orchestrator: Task '{name}' failed {fails} times today. Check executor log.")
    except Exception as e:
        log.error("Task failure watch: "+str(e))

# ── MAIN WATCH LOOP ───────────────────────────────────────────────────────────
def run_all_watches():
    """Run all watch conditions."""
    log.info("ops_orchestrator running watch cycle...")
    watch_consulting_inbox()
    watch_github_digest()
    watch_domain_scorer_score()
    watch_task_failures()
    watch_trading_executor_activity()
    log.info("Watch cycle complete")

def watch():
    init_db()
    tg("ops_orchestrator ONLINE — Ops Report active. Watching all conditions. Queuing tasks proactively.")
    log.info("ops_orchestrator watching — 5 minute cycles")
    run_all_watches()  # Run immediately on start
    while True:
        try:
            time.sleep(POLL)
            run_all_watches()
        except Exception as e:
            log.exception(e)

def show_status():
    """Show current watch state and recent actions."""
    c=sqlite3.connect(OPS_DB)
    actions=c.execute(
        "SELECT trigger_type,action_taken,created_at FROM ops_actions "
        "ORDER BY created_at DESC LIMIT 20").fetchall()
    c.close()
    print("\nOps Orchestrator — Recent Actions")
    print("="*50)
    if not actions:
        print("No actions taken yet.")
    for a in actions:
        print(f"{a[2][:16]} | {a[0]:<20} | {a[1][:40]}")
    print(f"\nPoll interval: {POLL}s | Watches: 7 active conditions")

if __name__=="__main__":
    p=argparse.ArgumentParser(description="ops_orchestrator — the agent network Ops Orchestration")
    p.add_argument("--watch",action="store_true",help="Run as daemon")
    p.add_argument("--status",action="store_true",help="Show watch status")
    p.add_argument("--test",action="store_true",help="Run one watch cycle")
    p.add_argument("--run",action="store_true",help="Run one watch cycle")
    args=p.parse_args()
    init_db()
    if args.watch:
        watch()
    elif args.status:
        show_status()
    elif args.test or args.run:
        log.info("Running one ops_orchestrator watch cycle...")
        run_all_watches()
        show_status()
    else:
        p.print_help()
