import os
import shlex,time,sqlite3,subprocess,requests,datetime,argparse,logging,json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/.env"),override=True)

GEMINI_KEY=os.getenv("GEMINI_API_KEY")
ANTHROPIC_KEY=os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT=os.getenv("TELEGRAM_CHAT_ID")
DB=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/task_watchdog.db")
LOG=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/action_queue.log")
POLL=60

logging.basicConfig(level=20,format="%(asctime)s [QUEUE] %(message)s",
    handlers=[logging.StreamHandler(),logging.FileHandler(LOG)])
log=logging.getLogger(__name__)

# ── GEMINI API ────────────────────────────────────────────────────────────────
def gemini(prompt,model="gemini-2.0-flash"):
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
    body={"contents":[{"parts":[{"text":prompt}]}]}
    try:
        r=requests.post(url,json=body,timeout=30)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        log.error("Gemini failed: "+str(e))
        return None

# ── CLAUDE FALLBACK (for complex reasoning only) ──────────────────────────────
def claude(prompt):
    try:
        import anthropic
        c=anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg=c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role":"user","content":prompt}])
        return msg.content[0].text
    except Exception as e:
        log.error("Claude fallback failed: "+str(e))
        return None

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg(m,urgent=False):
    full=("URGENT: " if urgent else "")+m
    log.info("[TG] "+full)
    try:
        requests.post(
            "https://api.telegram.org/bot"+TELEGRAM_TOKEN+"/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":full},timeout=10)
    except:pass

# ── TASK DB ───────────────────────────────────────────────────────────────────
def init_db():
    c=sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS tasks("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT,type TEXT,command TEXT,"
        "script_path TEXT,args TEXT,service TEXT,"
        "priority INTEGER DEFAULT 5,"
        "assigned_to TEXT DEFAULT 'executor',"
        "status TEXT DEFAULT 'pending',"
        "result TEXT,"
        "created_at TEXT DEFAULT(datetime('now')),"
        "updated_at TEXT)")
    # Add assigned_to column if it doesn't exist
    try:
        c.execute("ALTER TABLE tasks ADD COLUMN assigned_to TEXT DEFAULT 'executor'")
    except:pass
    c.commit()
    c.close()

def get_action_queue_tasks():
    try:
        c=sqlite3.connect(DB)
        c.row_factory=sqlite3.Row
        rows=[dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE status='pending' AND assigned_to='action_queue' "
            "ORDER BY priority ASC,created_at ASC").fetchall()]
        c.close()
        return rows
    except Exception as e:
        log.error("DB read: "+str(e))
        return []

def mark(tid,status,result=""):
    try:
        c=sqlite3.connect(DB)
        c.execute(
            "UPDATE tasks SET status=?,result=?,updated_at=? WHERE id=?",
            (status,result,datetime.datetime.now().isoformat(),tid))
        c.commit()
        c.close()
    except Exception as e:
        log.error("DB mark: "+str(e))

# ── QUEUE TASK EXECUTORS ────────────────────────────────────────────────────

def exec_generate(task):
    """Use Gemini to generate content — boilerplate, reports, summaries."""
    prompt=task.get("command","")
    if not prompt:
        return False,"No prompt specified"
    result=gemini(prompt)
    if not result:
        # Fallback to Claude Haiku only if Gemini fails
        log.info("Gemini unavailable — falling back to Claude Haiku")
        result=claude(prompt)
    if not result:
        return False,"Both Gemini and Claude failed"
    # Save to file if output_path specified in args
    args=task.get("args","")
    if args and args.startswith("save:"):
        path=os.path.expanduser(args.replace("save:","").strip())
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        Path(path).write_text(result)
        return True,f"Generated and saved to {path} ({len(result)} chars)"
    return True,result[:300]

# Commands this queue is permitted to run. Same policy as core/executor.py:
# argv form only, no shell, metacharacters refused.
ALLOWED_PREFIXES = tuple(
    x.strip() for x in os.getenv(
        "ALLOWED_COMMANDS",
        "systemctl,journalctl,git,npm,python3,pytest,ls,df,uptime"
    ).split(",") if x.strip()
)


def parse_command(command):
    """Split into argv, or None if not permitted."""
    if not command or any(ch in command for ch in ";|&><`$\n"):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or argv[0] not in ALLOWED_PREFIXES:
        return None
    return argv


def exec_shell(task):
    """Run an allow-listed command. Never through a shell."""
    argv=parse_command(task.get("command",""))
    if argv is None:
        return False,"Refused: command not on the allow-list"
    r=subprocess.run(argv,capture_output=True,text=True,timeout=120)
    ok=r.returncode==0
    return ok,(r.stdout if ok else r.stderr).strip()[:400]

def exec_file_write(task):
    """Write content to a file."""
    path=os.path.expanduser(task.get("args","").replace("path:","").strip())
    content=task.get("command","")
    if not path or not content:
        return False,"path and content required"
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(content)
    return True,f"Written: {path}"

def exec_batch_generate(task):
    """Generate multiple files from a JSON spec in command field."""
    try:
        spec=json.loads(task.get("command","{}"))
        items=spec.get("items",[])
        output_dir=os.path.expanduser(spec.get("output_dir",os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/action_queue_output")))
        Path(output_dir).mkdir(parents=True,exist_ok=True)
        saved=[]
        for item in items:
            prompt=item.get("prompt","")
            filename=item.get("filename","output.txt")
            result=gemini(prompt)
            if result:
                p=Path(output_dir)/filename
                p.write_text(result)
                saved.append(filename)
                time.sleep(0.5)
        return True,f"Generated {len(saved)}/{len(items)} files in {output_dir}"
    except Exception as e:
        return False,str(e)

ACTION_QUEUE_EXECUTORS={
    "generate":exec_generate,
    "gemini":exec_generate,
    "content":exec_generate,
    "shell":exec_shell,
    "bash":exec_shell,
    "file_write":exec_file_write,
    "batch_generate":exec_batch_generate,
}

def exec_task(task):
    tid=task["id"]
    name=task.get("name",f"action_queue-{tid}")
    ttype=task.get("type","generate")
    log.info(f"▶ [{ttype}] {name}")
    mark(tid,"running")
    executor=ACTION_QUEUE_EXECUTORS.get(ttype,exec_generate)
    try:
        ok,result=executor(task)
    except Exception as e:
        ok=False
        result=str(e)
    if ok:
        log.info(f"OK {name}: {result[:100]}")
        mark(tid,"done",result)
        tg(f"QUEUE DONE {name} - {result[:150]}")
    else:
        log.error(f"FAIL {name}: {result}")
        mark(tid,"failed",result)
        tg(f"QUEUE FAILED {name} - {result}",urgent=True)
    return ok

def drain():
    tasks=get_action_queue_tasks()
    if not tasks:
        log.info("action_queue queue empty")
        return 0
    log.info(f"action_queue queue: {len(tasks)} tasks")
    tg(f"action_queue — running {len(tasks)} task(s)")
    for t in tasks:
        exec_task(t)
        time.sleep(0.5)
    return len(tasks)

def watch():
    init_db()
    tg("action_queue ONLINE - Volume worker active. Gemini powered. Claude credits protected.")
    log.info("action_queue watching queue every 60s")
    while True:
        try:
            drain()
        except Exception as e:
            log.exception(e)
        time.sleep(POLL)

# ── TEST ──────────────────────────────────────────────────────────────────────
def test_gemini():
    log.info("Testing Gemini connection...")
    result=gemini("Say 'action_queue online' and nothing else.")
    if result:
        log.info(f"Gemini test: {result.strip()}")
        tg(f"action_queue test OK: {result.strip()[:80]}")
        return True
    tg("action_queue Gemini test FAILED",urgent=True)
    return False

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--watch",action="store_true")
    p.add_argument("--once",action="store_true")
    p.add_argument("--test",action="store_true")
    args=p.parse_args()
    init_db()
    if args.test:
        test_gemini()
    elif args.watch:
        watch()
    else:
        drain()
