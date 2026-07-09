import os,time,subprocess,requests,datetime,argparse,logging
from dotenv import load_dotenv
load_dotenv()
TOKEN=os.getenv("TELEGRAM_TOKEN")
CHAT=os.getenv("TELEGRAM_CHAT_ID")
LOG=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/verifier.log")
logging.basicConfig(level=20,format="%(asctime)s [V] %(message)s",
    handlers=[logging.StreamHandler(),logging.FileHandler(LOG)])
log=logging.getLogger(__name__)
SVCS=["trading_executor","domain_scorer","task_watchdog"]
def tg(m):
    log.info(m)
    try:
        requests.post(
            "https://api.telegram.org/bot"+TOKEN+"/sendMessage",
            json={"chat_id":CHAT,"text":m},timeout=10)
    except:pass
def chk(s):
    r=subprocess.run(["systemctl","is-active",s],capture_output=True,text=True)
    return r.stdout.strip()=="active"
def run_check():
    now=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    down=[s for s in SVCS if not chk(s)]
    live=[s for s in SVCS if chk(s)]
    log.info("Live:"+str(live)+" Down:"+str(down))
    if down:
        tg("RED ALERT "+now+" NOT RUNNING: "+",".join(down)+" Live: "+",".join(live))
    else:
        log.info("All services confirmed live")
        tg("All the agent network services live "+now)
def daemon():
    tg("Deployment Verifier ONLINE - 30min checks")
    run_check()
    while True:
        time.sleep(1800)
        run_check()
p=argparse.ArgumentParser()
p.add_argument("--daemon",action="store_true")
p.add_argument("--once",action="store_true")
args=p.parse_args()
if args.daemon:daemon()
else:run_check()
