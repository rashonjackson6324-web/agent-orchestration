import os,sys,time,sqlite3,subprocess,requests,datetime,argparse,logging,shlex,pathlib
from dotenv import load_dotenv
load_dotenv()
TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
DB=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/task_watchdog.db")
LOG=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/executor.log")
logging.basicConfig(level=20,format="%(asctime)s [E] %(message)s",
    handlers=[logging.StreamHandler(),logging.FileHandler(LOG)])
log=logging.getLogger(__name__)
def tg(m,urgent=False):
    full=("URGENT: " if urgent else "")+m
    log.info(full)
    try:
        requests.post(
            "https://api.telegram.org/bot"+TOKEN+"/sendMessage",
            json={"chat_id":CHAT,"text":full},timeout=10)
    except:pass
def init_db():
    c=sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS tasks("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT,type TEXT,command TEXT,"
        "script_path TEXT,args TEXT,service TEXT,"
        "priority INTEGER DEFAULT 5,"
        "status TEXT DEFAULT 'pending',"
        "result TEXT,"
        "created_at TEXT DEFAULT(datetime('now')),"
        "updated_at TEXT)")
    c.commit()
    c.close()
def get_pending():
    try:
        c=sqlite3.connect(DB)
        c.row_factory=sqlite3.Row
        rows=[dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE status='pending' "
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
# Commands the executor is permitted to run. A task whose command does not start
# with one of these is refused. An executor that will run anything a queue hands
# it is a remote shell, not an agent.
ALLOWED_PREFIXES = tuple(
    p.strip() for p in os.getenv(
        "ALLOWED_COMMANDS",
        "systemctl,journalctl,git,npm,python3,pytest,ls,df,uptime"
    ).split(",") if p.strip()
)


def parse_command(command: str):
    """Split a command into argv, or return None if it is not permitted.

    Shell metacharacters are rejected outright rather than escaped: a task queue
    that accepts pipes and semicolons is a remote shell wearing a costume.
    """
    if not command or any(ch in command for ch in ";|&><`$\n"):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or argv[0] not in ALLOWED_PREFIXES:
        return None
    return argv



# Scripts the executor may run must live under SCRIPT_ROOT. Path traversal out
# of it is refused. Combined with the argv form (no shell), this closes the
# "script_path is really a shell injection" hole.
SCRIPT_ROOT = pathlib.Path(os.getenv("SCRIPT_ROOT", ".")).resolve()


def resolve_script(script_path: str):
    if not script_path:
        return None
    try:
        candidate = (SCRIPT_ROOT / script_path).resolve()
    except OSError:
        return None
    if not candidate.is_file():
        return None
    if SCRIPT_ROOT not in candidate.parents and candidate != SCRIPT_ROOT:
        return None
    return candidate


def exec_task(task):
    tid=task["id"]
    name=task.get("name","Task"+str(tid))
    tt=task.get("type","shell")
    mark(tid,"running")
    try:
        if tt in("shell","bash","command"):
            # argv form, never shell=True. With shell=True an allow-list on the
            # first token is theatre: "systemctl status x; rm -rf /" passes it.
            command=task.get("command","")
            argv=parse_command(command)
            if argv is None:
                mark(tid,"refused")
                log.warning("Refused command not on the allow-list: %s", command[:80])
                return
            r=subprocess.run(argv,capture_output=True,text=True,timeout=120)
            ok=r.returncode==0
            result=(r.stdout if ok else r.stderr).strip()[:400]
        elif tt=="systemd_restart":
            svc=task.get("service","")
            subprocess.run(["sudo","systemctl","restart",svc],check=True,timeout=30)
            time.sleep(3)
            r=subprocess.run(["systemctl","is-active",svc],capture_output=True,text=True)
            ok=r.stdout.strip()=="active"
            result=svc+(" active" if ok else " FAILED")
        elif tt in("python","script"):
            # No shell. The script must resolve inside SCRIPT_ROOT, so a
            # script_path of "../../etc/x" or "; curl ... | sh" cannot escape.
            script=resolve_script(task.get("script_path",""))
            if script is None:
                mark(tid,"refused")
                log.warning("Refused script outside SCRIPT_ROOT: %s", task.get("script_path","")[:80])
                return
            argv=[sys.executable,str(script),*shlex.split(task.get("args","") or "")]
            r=subprocess.run(argv,capture_output=True,text=True,timeout=120)
            ok=r.returncode==0
            result=(r.stdout if ok else r.stderr).strip()[:400]
        else:
            ok=False
            result="Unknown type: "+tt
    except Exception as e:
        ok=False
        result=str(e)
    if ok:
        log.info("OK "+name+": "+result)
        mark(tid,"done",result)
        tg("DONE "+name+" - "+result)
    else:
        log.error("FAIL "+name+": "+result)
        mark(tid,"failed",result)
        tg("FAILED "+name+" - "+result,urgent=True)
    return ok
def drain():
    tasks=get_pending()
    if not tasks:
        log.info("Queue empty")
        return 0
    tg("Auto Executor - running "+str(len(tasks))+" task(s)")
    for t in tasks:
        exec_task(t)
        time.sleep(1)
    return len(tasks)
def watch():
    init_db()
    tg("Auto Executor ONLINE - no permission needed. Results only.")
    while True:
        try:drain()
        except Exception as e:log.exception(e)
        time.sleep(60)
p=argparse.ArgumentParser()
p.add_argument("--watch",action="store_true")
p.add_argument("--once",action="store_true")
args=p.parse_args()
init_db()
if args.watch:watch()
else:drain()
