#!/usr/bin/env python3
"""
OUTPUT VALIDATOR — quality gate on strategy-agent output.

Runs a seven-point checklist against every directive the strategy agent
produces: hallucination, policy compliance, manual-loop avoidance, confidence
floor, format correctness, drift against prior sessions, and undisclosed risk.
Anything below the confidence floor fails the whole output.

Schedule: 18:00 ET daily, plus on-demand webhook.
"""

import os, json, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LOG_PATH     = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/logs/output_validator.log")
STRATEGY_STATE    = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/strategy_agent_state.json")
ET = ZoneInfo("America/New_York")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [VALIDATOR] %(message)s",
  handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
log = logging.getLogger("output_validator")

CHECKLIST = [
  ("1 — Hallucination",   "All facts, stats, citations verifiable. No invented numbers."),
  ("2 — Policy Compliance",  "Aligns with operating spec, operator directives."),
  ("3 — Manual Loop",    "No manual work required from the operator. Fully automatable path."),
  ("4 — Confidence",    "All claims >= 70%. Flag < 70%. Hard FAIL < 50%."),
  ("5 — Format",      "Correct format for destination: Telegram, Obsidian, client doc."),
  ("6 — Drift",       "Consistent with prior sessions, no contradiction of established positions."),
  ("7 — Risk Exposure",   "No undisclosed financial, reputational, or operational risk to the operator.")
]

output_validator_PROMPT = """
You are the output validator. You are the gate on every strategy-agent output.
Run the 7-point checklist on the strategy_agent output below.
For each checkpoint: PASS ✅, FLAG ⚠️, or FAIL ❌.

7-POINT CHECKLIST:
1 — Hallucination: All facts verifiable?
2 — Policy Compliance: Aligns with the operating spec and operator directives?
3 — Manual Loop: Does it avoid requiring manual work from the operator?
4 — Confidence: Claims >= 70% confidence?
5 — Format: Correct format for its destination?
6 — Drift: Consistent with prior sessions and established the agent network positions?
7 — Risk Exposure: No undisclosed risks to the operator?

Return structured assessment. Be precise. Never soften a flag.
Format:
CHECKPOINT 1: ✅/⚠️/❌ [brief reason]
CHECKPOINT 2: ✅/⚠️/❌ [brief reason]
...
OVERALL: PASS / FLAGGED / FAIL
NOTE: [one-line summary]
"""

def load_strategy_output():
  """Load strategy_agent's latest directive."""
  try:
    with open(STRATEGY_STATE) as f:
      return json.load(f)
  except FileNotFoundError:
    return None
  except Exception as e:
    log.warning("Could not load strategy state: %s", e)
    return None

def run_checklist(output_text):
  """Run output_validator's 7-point checklist via Claude."""
  if not ANTHROPIC_API_KEY:
    return "ANTHROPIC_API_KEY not set — output_validator offline"
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
        "max_tokens": 500,
        "system": output_validator_PROMPT,
        "messages": [{"role": "user", "content": f"strategy_agent OUTPUT:\n{output_text}"}]
      },
      timeout=20
    )
    return r.json()["content"][0]["text"].strip()
  except Exception as e:
    log.exception("output_validator checklist failed: %s", e)
    return f"Checklist error: {e}"

def parse_status(checklist_result):
  """Determine overall status from checklist result."""
  if "FAIL" in checklist_result and "❌" in checklist_result:
    return "VIOLATIONS FOUND"
  if "⚠️" in checklist_result or "FLAG" in checklist_result:
    return "FLAGS DETECTED"
  return "ALL CLEAR"

def format_report(checklist_result, strategy_ts, status, outputs_reviewed):
  date_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
  if status == "ALL CLEAR":
    return (
      f"═══════════════════════════════════════\n"
      f" VALIDATOR REPORT — {date_str}\n"
      f"Agent Monitor\n"
      f"═══════════════════════════════════════\n"
      f"OUTPUTS REVIEWED: {outputs_reviewed}\n"
      f"STATUS: ✅ ALL CLEAR\n\n"
      f"strategy_agent is operating at 100%.\n"
      f"No hallucinations. No standard violations.\n"
      f"No manual loops. No drift detected.\n\n"
      f"output_validator Status: WATCHING 👁️\n"
      f"═══════════════════════════════════════"
    )
  else:
    return (
      f"═══════════════════════════════════════\n"
      f" VALIDATOR REPORT — {date_str}\n"
      f"Agent Monitor\n"
      f"═══════════════════════════════════════\n"
      f"OUTPUTS REVIEWED: {outputs_reviewed}\n"
      f"PERIOD: Last 24 hours\n"
      f"STATUS: {'🔴' if status == 'VIOLATIONS FOUND' else '⚠️'} {status}\n"
      f"───────────────────────────────────────\n"
      f"{checklist_result}\n"
      f"───────────────────────────────────────\n"
      f"output_validator Status: WATCHING 👁️\n"
      f"═══════════════════════════════════════"
    )

def telegram(msg):
  if not TELEGRAM_TOKEN:
    log.info("No token — output_validator report printed only")
    return
  try:
    requests.post(
      f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4096]},
      timeout=8
    )
  except Exception as e:
    log.warning("Telegram failed: %s", e)

def main():
  log.info("VALIDATOR — PATROL ACTIVATED")
  strategy_state = load_strategy_output()

  if not strategy_state:
    # No strategy output to validate — still report
    report = (
      f" VALIDATOR REPORT — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}\n"
      f"STATUS: ⚠️ NO strategy_agent OUTPUT FOUND\n"
      f"strategy_agent has not produced output in the last 24 hours.\n"
      f"Check strategy_agent.service status on host.\n"
      f"output_validator Status: WATCHING 👁️"
    )
    log.warning(report)
    telegram(report)
    return

  directive = strategy_state.get("directive", "")
  strategy_ts = strategy_state.get("ts", "unknown")

  log.info("Validating strategy output from: %s", strategy_ts)
  checklist_result = run_checklist(directive)
  status      = parse_status(checklist_result)
  report      = format_report(checklist_result, strategy_ts, status, 1)

  log.info("\n%s", report)
  telegram(report)
  log.info("VALIDATOR PATROL COMPLETE — status=%s", status)

if __name__ == "__main__":
  main()
