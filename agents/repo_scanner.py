#!/usr/bin/env python3
"""
REPO SCANNER — GitHub Intelligence Agent
Scans trending repos against your stack profile.
Scores opportunities. Feeds domain_scorer.
GCP host: ~/.agent-network/repo_scanner.py
Fires: 6AM ET daily
"""
import os, json, logging, requests
from datetime import datetime
from zoneinfo import ZoneInfo

GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
LOG_PATH          = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/logs/repo_scanner.log")
OUT_PATH          = os.path.expanduser(os.getenv("AGENT_HOME", "~/.agent-network") + "/data/repo_scanner_latest.json")
ET                = ZoneInfo("America/New_York")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [REPO_SCAN] %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
log = logging.getLogger("repo_scanner")

NETWORK_WATCH_TOPICS = [
    "trading-bot", "mcp-server", "multi-agent", "claude-agent",
    "agent-orchestration", "alpaca-trading", "python-trading",
    "agent-observability", "n8n", "crewai", "langgraph"
]

NETWORK_WATCHED_REPOS = [
    r for r in os.getenv("WATCHED_REPOS", "").split(",") if r.strip()
]

def gh_headers():
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "the agent network-repo_scanner/1.0"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"token {GITHUB_TOKEN}"
    return h

def get_trending(topic, limit=5):
    try:
        r = requests.get(
            "https://api.github.com/search/repositories",
            headers=gh_headers(),
            params={
                "q": f"topic:{topic} pushed:>2026-01-01",
                "sort": "stars", "order": "desc", "per_page": limit
            },
            timeout=10
        )
        if r.status_code == 200:
            return r.json().get("items", [])
    except Exception as e:
        log.warning("Trending fetch for %s failed: %s", topic, e)
    return []

def check_watched_repos():
    updates = []
    for repo in NETWORK_WATCHED_REPOS:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo}",
                headers=gh_headers(), timeout=8
            )
            if r.status_code == 200:
                data = r.json()
                updates.append({
                    "repo": repo,
                    "stars": data.get("stargazers_count", 0),
                    "updated": data.get("updated_at", ""),
                    "description": data.get("description", "")
                })
        except Exception as e:
            log.debug("Repo check failed %s: %s", repo, e)
    return updates

def score_opportunity(repo, topic):
    """Score repo relevance to the configured stack."""
    score = 0
    name  = (repo.get("full_name", "") + " " + repo.get("description", "")).lower()
    stars = repo.get("stargazers_count", 0)

    # Star scoring
    if stars > 10000: score += 30
    elif stars > 1000: score += 20
    elif stars > 100: score += 10

    # relevance keywords
    relevance_terms = ["trading", "agent", "claude", "mcp", "alpaca", "signal",
                 "webhook", "telegram", "n8n", "supabase", "autonomous"]
    score += sum(5 for term in relevance_terms if term in name)

    # Topic bonus
    high_value = ["mcp-server", "multi-agent", "trading-bot", "claude-agent"]
    if topic in high_value:
        score += 15

    return min(100, score)

def build_report(all_repos, watched_updates, date_str):
    top = sorted(all_repos, key=lambda x: x.get("score", 0), reverse=True)[:10]
    lines = [
        "═══════════════════════════════════════",
        f"⚔️  REPO_SCAN INTEL — {date_str}",
        f"Topics scanned: {len(NETWORK_WATCH_TOPICS)} | Repos found: {len(all_repos)}",
        "═══════════════════════════════════════",
        "",
        "── TOP OPPORTUNITIES ──────────────────",
    ]
    for r in top:
        lines.append(
            f"⭐ {r['score']:>3}/100 | {r['full_name']} ({r['stars']} ⭐)\n"
            f"   {(r.get('description','') or '')[:70]}"
        )

    lines += ["", "── WATCHED REPOS ──────────────────────"]
    for w in watched_updates:
        lines.append(f"  {w['repo']}: {w['stars']} ⭐ | {w['updated'][:10]}")

    return "\n".join(lines)

def main():
    log.info("REPO_SCAN AWAKENS — scanning GitHub")
    date_str  = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    all_repos = []

    for topic in NETWORK_WATCH_TOPICS[:6]:  # limit to avoid rate limits
        repos = get_trending(topic, limit=3)
        for r in repos:
            r["topic"]  = topic
            r["score"]  = score_opportunity(r, topic)
            r["stars"]  = r.get("stargazers_count", 0)
            all_repos.append(r)
        log.info("Topic %s: %d repos", topic, len(repos))

    watched = check_watched_repos()
    report  = build_report(all_repos, watched, date_str)

    log.info("\n%s", report)

    if TELEGRAM_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": report[:4000]},
                timeout=8
            )
        except Exception as e:
            log.warning("Telegram failed: %s", e)

    # Save for domain_scorer feed
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump({
            "date": date_str,
            "repos": [{"name": r["full_name"], "score": r["score"], "stars": r["stars"]}
                      for r in sorted(all_repos, key=lambda x: x.get("score",0), reverse=True)[:15]],
            "watched": watched
        }, f, indent=2)

    log.info("REPO_SCAN COMPLETE")

if __name__ == "__main__":
    main()
