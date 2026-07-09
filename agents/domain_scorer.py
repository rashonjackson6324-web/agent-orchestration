#!/usr/bin/env python3
"""
DOMAIN SCORER — daily multi-domain health scorer.

Scores each configured domain 0-100 via LLM, optionally grounded with live web
search, then emits a weighted composite. Domains are defined in
config/domains.json (see config/domains.example.json).

Schedule: 07:00 ET daily via systemd timer.
"""

import os, json, pathlib, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

# ── CONFIG ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "")
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
LOG_PATH           = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/logs/domain_scorer.log")

ET = ZoneInfo("America/New_York")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCORER] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
log = logging.getLogger("domain_scorer")

# -- DOMAIN DEFINITIONS -------------------------------------------------------
# Domains are configuration, not code. They encode what *your* operation cares
# about, so they live outside the repo. See config/domains.example.json.
DOMAINS_PATH = pathlib.Path(os.getenv("DOMAINS_CONFIG", "config/domains.json"))


def load_domains() -> list:
    if not DOMAINS_PATH.exists():
        raise FileNotFoundError(
            f"No domain config at {DOMAINS_PATH}. Copy config/domains.example.json "
            "to config/domains.json and edit it, or set DOMAINS_CONFIG."
        )
    domains = json.loads(DOMAINS_PATH.read_text(encoding="utf-8"))
    total = sum(d["weight"] for d in domains)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"Domain weights must sum to 1.0, got {total}")
    return domains


DOMAINS = load_domains()

# ── PERPLEXITY SONAR (7th domain) ─────────────────────────────────────────────
def get_market_intelligence_score():
    """Domain 7: Live grounded market intel via Perplexity Sonar."""
    if not PERPLEXITY_API_KEY:
        return {"score": 50, "note": "Perplexity API key not set — live intel unavailable"}

    query = (
        "What are the most important AI agent and trading developments in the last 24 hours "
        "relevant to: autonomous trading bots, AI agent frameworks, sports analytics SaaS, "
        "remote work market, MCP ecosystem, Claude API updates? "
        "Score the opportunity level for a solo AI operator 0-100 and give one key insight."
    )
    try:
        r = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": "Respond with JSON only: {\"score\": N, \"note\": \"one line insight\"}"},
                    {"role": "user", "content": query}
                ]
            },
            timeout=20
        )
        content = r.json()["choices"][0]["message"]["content"]
        clean   = content.strip().replace("```json", "").replace("```", "")
        return json.loads(clean)
    except Exception as e:
        log.warning("Perplexity sonar failed: %s", e)
        return {"score": 55, "note": "Live intel temporarily unavailable"}

# ── CLAUDE SCORER ─────────────────────────────────────────────────────────────
def score_domain(domain):
    """Score a single domain via Claude API."""
    if not ANTHROPIC_API_KEY:
        return {"score": 50, "note": "Anthropic key not set"}
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
                "max_tokens": 150,
                "system": "You are a scoring agent. Score the requested domain. Return only valid JSON.",
                "messages": [{"role": "user", "content": domain["prompt"]}]
            },
            timeout=15
        )
        content = r.json()["content"][0]["text"].strip()
        clean   = content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean)
    except Exception as e:
        log.warning("Score failed for %s: %s", domain["key"], e)
        return {"score": 50, "note": "Scoring error"}

# ── TOP 3 ACTIONS ─────────────────────────────────────────────────────────────
def get_top_actions(scores):
    if not ANTHROPIC_API_KEY:
        return ["1. Review the lowest-scoring domain",
                "2. Clear the oldest open task",
                "3. Verify all scheduled agents reported today"]
    summary = json.dumps({d["key"]: scores.get(d["key"], {}) for d in DOMAINS}, indent=2)
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
                "max_tokens": 300,
                "system": "Given domain scores, identify the 3 highest-leverage actions for today. Be specific. No generic advice.",
                "messages": [{"role": "user", "content": f"Domain scores:\n{summary}\n\nReturn 3 numbered actions."}]
            },
            timeout=15
        )
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        log.warning("Top actions failed: %s", e)
        return "Actions unavailable"

# ── PROGRESS BAR ──────────────────────────────────────────────────────────────
def bar(score, width=10):
    filled = int(score / 100 * width)
    return "█" * filled + "░" * (width - filled)

# ── RENDER REPORT ─────────────────────────────────────────────────────────────
def render_report(scores, composite, top_actions, date_str):
    lines = [
        "═══════════════════════════════════════",
        f"⚡ SCORER DAILY BRIEF — {date_str}",
        "the agent network",
        "═══════════════════════════════════════",
        "",
        f"COMPOSITE SCORE: {composite:.0f}/100 {bar(composite)}",
        "",
        "── DOMAIN BREAKDOWN ───────────────────",
    ]
    for d in DOMAINS:
        s = scores.get(d["key"], {})
        sc = s.get("score", 0)
        note = s.get("note", "")
        lines.append(f"{d['label']:<18} {sc:>3}/100 {bar(sc, 8)}")
        if note:
            lines.append(f"  └─ {note}")

    # Domain 7
    mi = scores.get("market_intelligence", {})
    mi_score = mi.get("score", 0)
    lines.append(f"{'Market Intel (Live)':<18} {mi_score:>3}/100 {bar(mi_score, 8)} 🌐")
    if mi.get("note"):
        lines.append(f"  └─ {mi.get('note')}")

    lines += [
        "",
        "── TOP 3 ACTIONS TODAY ────────────────",
        str(top_actions),
        "",
        "═══════════════════════════════════════",
        f"Target: 80/100 | {'✅ ON TRACK' if composite >= 80 else '⚠️ BELOW TARGET'}",
    ]
    return "\n".join(lines)

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_telegram(msg):
    if not TELEGRAM_TOKEN:
        log.info("No Telegram token. Report printed only.")
        return
    chunks = [msg[i:i+4000] for i in range(0, len(msg), 4000)]
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                timeout=8
            )
        except Exception as e:
            log.warning("Telegram chunk failed: %s", e)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("SCORER AWAKENS — scoring begins")
    date_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    scores   = {}

    for domain in DOMAINS:
        log.info("Scoring domain: %s", domain["key"])
        result = score_domain(domain)
        scores[domain["key"]] = result

    # Domain 7 — live Perplexity
    log.info("Scoring domain: market_intelligence (Perplexity Sonar)")
    scores["market_intelligence"] = get_market_intelligence_score()

    # Weighted composite (6 primary domains)
    composite = sum(
        scores.get(d["key"], {}).get("score", 50) * d["weight"]
        for d in DOMAINS
    )

    top_actions = get_top_actions(scores)
    report      = render_report(scores, composite, top_actions, date_str)

    log.info("\n%s", report)
    send_telegram(report)

    # Save to disk for other agents
    out_path = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/domain_scores_latest.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "date": date_str,
            "composite": round(composite, 1),
            "domains": scores,
            "top_actions": str(top_actions)
        }, f, indent=2)

    log.info("SCORER COMPLETE — composite=%.1f", composite)

if __name__ == "__main__":
    main()
