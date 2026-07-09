#!/usr/bin/env python3
"""
STRATEGY AGENT — produces one decisive daily directive.

Loads organizational context from config/strategy_prompt.md (private, gitignored),
combines it with the latest domain scores, and emits a single directive:
situation, priority, blocker, action. Output is validated downstream by
output_validator before it is trusted.

Schedule: 08:00 ET daily via systemd timer.
"""

import os, json, pathlib, logging, requests, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
LOG_PATH     = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/logs/strategy_agent.log")
STATE_PATH    = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/strategy_agent_state.json")
ET = ZoneInfo("America/New_York")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
  level=logging.INFO,
  format="%(asctime)s [STRATEGY] %(message)s",
  handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
log = logging.getLogger("strategy_agent")

# -- SYSTEM PROMPT ------------------------------------------------------------
# The strategy prompt describes your organization: departments, current state,
# operating standards. That is private business context, not source code.
# See config/strategy_prompt.example.md.
PROMPT_PATH = pathlib.Path(os.getenv("STRATEGY_PROMPT", "config/strategy_prompt.md"))


def load_system_prompt() -> str:
  if not PROMPT_PATH.exists():
    raise FileNotFoundError(
      f"No strategy prompt at {PROMPT_PATH}. Copy "
      "config/strategy_prompt.example.md to config/strategy_prompt.md."
    )
  return PROMPT_PATH.read_text(encoding="utf-8")


SYSTEM_PROMPT = load_system_prompt()

def load_context():
  """Load the latest domain scores as context."""
  ctx = {}
  try:
    with open(os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/domain_scores_latest.json")) as f:
      ctx["domain_scorer"] = json.load(f)
  except Exception:
    ctx["domain_scorer"] = {"composite": 60, "note": "domain_scorer data unavailable"}


  return ctx

def call_claude(context):
  if not ANTHROPIC_API_KEY:
    return "ANTHROPIC_API_KEY not set — strategy_agent offline"
  date_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
  user_msg = (
    f"Date: {date_str}\n"
    f"domain_scorer Composite: {context.get('domain_scorer', {}).get('composite', '?')}/100\n"
    "Generate today's directive."
  )
  try:
    r = requests.post(
      "https://api.anthropic.com/v1/messages",
      headers={
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
      },
      json={
        "model": "claude-sonnet-4-6",
        "max_tokens": 600,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_msg}]
      },
      timeout=30
    )
    return r.json()["content"][0]["text"].strip()
  except Exception as e:
    log.exception("Claude call failed: %s", e)
    return f"CEO directive unavailable: {e}"

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

def main():
  log.info("strategy_agent — CEO DIRECTIVE CYCLE")
  context  = load_context()
  directive = call_claude(context)

  header = (
    " strategy_agent — CEO DIRECTIVE\n"
    f"the agent network | {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}\n"
    "─────────────────────────────────\n"
  )
  full_msg = header + directive

  log.info(full_msg)
  telegram(full_msg)

  # Save state for output_validator validation
  state = {
    "ts": datetime.now(ET).isoformat(),
    "directive": directive,
    "context_composite": context.get("domain_scorer", {}).get("composite", 0)
  }
  os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
  with open(STATE_PATH, "w") as f:
    json.dump(state, f, indent=2)

  log.info("strategy_agent COMPLETE")

if __name__ == "__main__":
  main()
