#!/usr/bin/env python3
"""
CONFIDENCE GATE — the skepticism layer.

Sits between every agent output and every downstream action.

    >= 70%   pass, act on it
    50-69%   flag, surface for review, do not act
    <  50%   hard stop, discard and alert

Usable as a library, a one-shot CLI check, or a daemon watching a queue.
"""

import os,sys,time,sqlite3,json,requests,datetime,argparse,logging,re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/.env"),override=True)

ANTHROPIC_KEY=os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN=os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT=os.getenv("TELEGRAM_CHAT_ID")
DB=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/task_watchdog.db")
LOG=os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/confidence_gate.log")
POLL=30

logging.basicConfig(level=20,format="%(asctime)s [confidence_gate] %(message)s",
    handlers=[logging.StreamHandler(),logging.FileHandler(LOG)])
log=logging.getLogger(__name__)

# ── VERDICTS ──────────────────────────────────────────────────────────────────
PASS="PASS"
FLAG="FLAG"
STOP="STOP"

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def tg(m,urgent=False):
    full=("🔴 QC ALERT: " if urgent else "⚡ QC: ")+m
    log.info(full)
    try:
        requests.post(
            "https://api.telegram.org/bot"+TELEGRAM_TOKEN+"/sendMessage",
            json={"chat_id":TELEGRAM_CHAT,"text":full},timeout=10)
    except:pass

# ── CLAUDE HAIKU (QC brain) ───────────────────────────────────────────────────
def haiku(prompt,max_tokens=400):
    try:
        import anthropic
        c=anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        msg=c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role":"user","content":prompt}])
        return msg.content[0].text.strip()
    except Exception as e:
        log.error("Haiku failed: "+str(e))
        return None

# ── validator — Output Validator ───────────────────────────────────────────────
def validator(output,source,context=""):
    """
    Checks output for accuracy, completeness, hallucinations.
    Returns (confidence_0_to_100, issues_list)
    """
    prompt=f"""You are validator, an output validator for an autonomous AI trading system.
Evaluate this output from agent '{source}' for quality and accuracy.

OUTPUT TO VALIDATE:
{output[:2000]}

CONTEXT: {context or 'None provided'}

Respond with ONLY a JSON object:
{{
  "confidence": <integer 0-100>,
  "issues": ["issue1","issue2"],
  "verdict": "PASS|FLAG|STOP",
  "reason": "one sentence explanation"
}}

Confidence guide:
- 90-100: Complete, accurate, no issues
- 70-89: Minor issues, safe to pass
- 50-69: Notable issues, needs human review
- 0-49: Major problems, hallucination, wrong format, dangerous

Be strict for trading outputs. Be lenient for content generation."""

    result=haiku(prompt)
    if not result:
        return 0,["validator unavailable"],[],STOP
    try:
        # Strip any markdown fences
        clean=re.sub(r'```json|```','',result).strip()
        data=json.loads(clean)
        conf=int(data.get("confidence",0))
        issues=data.get("issues",[])
        verdict=data.get("verdict",STOP)
        reason=data.get("reason","")
        return conf,issues,reason,verdict
    except:
        log.error(f"validator parse failed: {result[:100]}")
        return 50,["Parse error"],result,FLAG

# ── validator — Schema Enforcer ───────────────────────────────────────────
def structure_check(output,expected_type):
    """
    Checks output matches expected schema/format.
    Returns (passes: bool, issues: list)
    """
    schemas={
        "trade_signal": ["ticker","direction","entry","stop","target"],
        "daily_report": ["composite","domains","actions"],
        "task": ["name","type","command"],
        "summary": [],  # No strict schema — just check it's non-empty prose
        "code": [],     # Just check it's non-empty
        "json": [],     # Check valid JSON
    }

    required=schemas.get(expected_type,[])
    issues=[]

    # Empty check
    if not output or len(output.strip())<10:
        return False,["Output is empty or too short"]

    # Error pattern check
    error_patterns=[
        "unavailable","error","failed","exception",
        "i cannot","i can't","i don't have","i'm unable"
    ]
    lower=output.lower()
    for p in error_patterns:
        if p in lower:
            issues.append(f"Output contains error pattern: '{p}'")

    # Schema field check
    for field in required:
        if field.lower() not in lower:
            issues.append(f"Missing required field: {field}")

    # JSON check
    if expected_type=="json":
        try:
            json.loads(output)
        except:
            issues.append("Output is not valid JSON")

    return len(issues)==0, issues

# ── validator — Cross-Reference Checker ───────────────────────────────────────
def fact_check(output,source,known_facts=None):
    """
    Checks for internal contradictions and obvious factual errors.
    Returns (passes: bool, flags: list)
    """
    flags=[]

    # Check for contradictory patterns
    contradiction_pairs=[
        ("buy","sell"),
        ("long","short"),
        ("increase","decrease"),
        ("profit","loss"),
    ]
    lower=output.lower()
    for a,b in contradiction_pairs:
        if a in lower and b in lower:
            # Only flag if they appear close together (within 100 chars)
            ai=lower.find(a)
            bi=lower.find(b)
            if abs(ai-bi)<100:
                flags.append(f"Possible contradiction: '{a}' and '{b}' appear close together")

    # Check for extreme/suspicious values in trading outputs
    if source in ("trading_executor",):
        numbers=re.findall(r'\b\d+\.?\d*\b',output)
        for n in numbers:
            try:
                val=float(n)
                if val>10000 and "%" not in output[max(0,output.find(n)-5):output.find(n)+len(n)+5]:
                    flags.append(f"Unusually large value detected: {val} — verify before acting")
            except:pass

    return len(flags)==0, flags

# ── validator — Re-Prompt Engine ─────────────────────────────────────────
def repair_attempt(original_prompt,failed_output,issues,source,attempt=1):
    """
    Rewrites a failed prompt and retries.
    Returns improved prompt string.
    """
    if attempt>3:
        return None  # Give up after 3 attempts

    rewrite_prompt=f"""You are validator, a prompt rewriter. An AI agent produced bad output.
Rewrite the original prompt to fix the issues.

ORIGINAL PROMPT: {original_prompt[:500]}
FAILED OUTPUT: {failed_output[:300]}
ISSUES FOUND: {json.dumps(issues)}
ATTEMPT: {attempt} of 3

Write ONLY the improved prompt. No explanation. No preamble."""

    return haiku(rewrite_prompt)

# ── DB — Validation Queue ─────────────────────────────────────────────────────
def init_db():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS validations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        output_text TEXT,
        expected_type TEXT DEFAULT 'summary',
        original_prompt TEXT,
        confidence INTEGER,
        verdict TEXT,
        issues TEXT,
        reason TEXT,
        retry_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT(datetime('now')),
        resolved_at TEXT)""")
    c.commit()
    c.close()

def queue_validation(source,output_text,expected_type="summary",original_prompt=""):
    """Queue an output for validation. Called by other agents."""
    c=sqlite3.connect(DB)
    c.execute(
        "INSERT INTO validations(source,output_text,expected_type,original_prompt) VALUES(?,?,?,?)",
        (source,output_text,expected_type,original_prompt))
    vid=c.lastrowid
    c.commit()
    c.close()
    log.info(f"Queued validation #{vid} from {source}")
    return vid

def get_pending_validations():
    try:
        c=sqlite3.connect(DB)
        c.row_factory=sqlite3.Row
        rows=[dict(r) for r in c.execute(
            "SELECT * FROM validations WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()]
        c.close()
        return rows
    except Exception as e:
        log.error("DB read: "+str(e))
        return []

def update_validation(vid,verdict,confidence,issues,reason,status):
    try:
        c=sqlite3.connect(DB)
        c.execute(
            "UPDATE validations SET verdict=?,confidence=?,issues=?,reason=?,status=?,resolved_at=? WHERE id=?",
            (verdict,confidence,json.dumps(issues),reason,status,
             datetime.datetime.now().isoformat(),vid))
        c.commit()
        c.close()
    except Exception as e:
        log.error("DB update: "+str(e))

# ── confidence_gate — Commander ────────────────────────────────────────────────────────
def validate(output,expected_type="summary",source="unknown",
             original_prompt="",context="",known_facts=None):
    """
    Main entry point. Routes output through all confidence gate.
    Returns dict with verdict, confidence, issues, and approved output.
    """
    log.info(f"confidence_gate validating output from {source} (type={expected_type})")

    all_issues=[]
    final_verdict=PASS

    # validator — Schema check first (fast, no API call)
    structure_passes,bb_issues=structure_check(output,expected_type)
    if not structure_passes:
        all_issues.extend(bb_issues)
        log.info(f"validator flagged: {bb_issues}")

    # validator — Contradiction check (fast, no API call)
    fact_passes,fact_flags=fact_check(output,source,known_facts)
    if not fact_passes:
        all_issues.extend(fact_flags)
        log.info(f"validator flagged: {fact_flags}")

    # validator — Deep quality check (API call)
    conf,cq_issues,reason,cq_verdict=validator(output,source,context)
    all_issues.extend(cq_issues)

    # Determine final verdict
    if conf>=70 and structure_passes and fact_passes:
        final_verdict=PASS
    elif conf>=50:
        final_verdict=FLAG
    else:
        final_verdict=STOP

    result={
        "verdict":final_verdict,
        "confidence":conf,
        "issues":all_issues,
        "reason":reason,
        "source":source,
        "output":output,
        "approved":final_verdict==PASS,
    }

    # Act on verdict
    if final_verdict==PASS:
        log.info(f"PASS — {source} output approved (confidence={conf}%)")

    elif final_verdict==FLAG:
        msg=(f"Output from {source} needs review\n"
             f"Confidence: {conf}%\n"
             f"Issues: {chr(10).join(all_issues[:3])}\n"
             f"Preview: {output[:150]}")
        tg(msg,urgent=True)
        log.warning(f"FLAG — {source} output flagged to the operator")

    elif final_verdict==STOP:
        msg=(f"Output from {source} BLOCKED by confidence_gate\n"
             f"Confidence: {conf}%\n"
             f"Issues: {chr(10).join(all_issues[:3])}")
        tg(msg,urgent=True)
        log.error(f"STOP — {source} output blocked")

    return result

def process_validation_queue():
    """Process all pending validations from the DB queue."""
    pending=get_pending_validations()
    if not pending:
        return 0
    log.info(f"Processing {len(pending)} validations")
    for v in pending:
        result=validate(
            output=v["output_text"],
            expected_type=v.get("expected_type","summary"),
            source=v.get("source","unknown"),
            original_prompt=v.get("original_prompt",""),
        )
        status="approved" if result["approved"] else ("flagged" if result["verdict"]==FLAG else "blocked")
        update_validation(
            v["id"],result["verdict"],result["confidence"],
            result["issues"],result["reason"],status)
        time.sleep(0.5)
    return len(pending)

def watch():
    init_db()
    tg("confidence_gate ONLINE — QC Army active. No output passes without approval.")
    log.info("confidence_gate watching validation queue")
    while True:
        try:
            process_validation_queue()
        except Exception as e:
            log.exception(e)
        time.sleep(POLL)

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--watch",action="store_true",help="Watch validation queue")
    p.add_argument("--test",action="store_true",help="Run self-test")
    p.add_argument("--input",type=str,help="Output text to validate")
    p.add_argument("--type",type=str,default="summary",help="Expected output type")
    p.add_argument("--source",type=str,default="test",help="Source agent name")
    args=p.parse_args()

    init_db()

    if args.test:
        log.info("Running confidence_gate self-test...")
        # Test 1: Good output
        r=validate(
            output="The strategy involves buying XAUUSD at 3320 with a stop at 3310 and target at 3340.",
            expected_type="trade_signal",
            source="trading_executor_test")
        log.info(f"Test 1 (good trade): verdict={r['verdict']} confidence={r['confidence']}%")
        # Test 2: Bad output
        r=validate(
            output="unavailable error failed",
            expected_type="summary",
            source="test_bad")
        log.info(f"Test 2 (bad output): verdict={r['verdict']} confidence={r['confidence']}%")
        tg(f"confidence_gate self-test complete. QC Army operational.")
        log.info("Self-test done")

    elif args.watch:
        watch()

    elif args.input:
        r=validate(args.input,args.type,args.source)
        print(json.dumps(r,indent=2))

    else:
        p.print_help()
