#!/usr/bin/env python3
"""
GitHub Scanner — repository intelligence
Scans GitHub for repos matching your stack profile and scores them for integration.
Outputs a weekly digest to the knowledge vault.

Usage:
  python github_scanner.py
  python github_scanner.py --dry-run
  python github_scanner.py --topic trading-bot --limit 20

Dependencies:
  pip install requests anthropic python-dotenv

Environment (.env):
  GITHUB_TOKEN=your_pat_token
  ANTHROPIC_API_KEY=your_key
  OBSIDIAN_VAULT_PATH=/path/to/vault   (optional — omit to print to stdout)
"""
import pathlib
import os
import sys
import json
import time
import argparse
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")

GITHUB_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# the stack context — what trading_executor scores against
NETWORK_PROFILE = (
    pathlib.Path(os.getenv("STACK_PROFILE", "config/stack_profile.md")).read_text(encoding="utf-8")
    if pathlib.Path(os.getenv("STACK_PROFILE", "config/stack_profile.md")).exists()
    else "Describe your current stack and priorities in config/stack_profile.md."
)

# Topics to scan — ordered by priority
SCAN_TOPICS = [
    "trading-bot",
    "mcp-server",
    "multi-agent",
    "claude-agent",
    "agent-orchestration",
    "websocket-trading",
    "broker-api",
    "algorithmic-trading",
    "langgraph",
    "crewai",
    "agent-observability",
    "react-dashboard",
    "n8n",
    "alpaca-trading",
    "python-trading",
]

# Hard disqualifiers — auto-SKIP without scoring
BANNED_LICENSES = ["GPL-2.0", "GPL-3.0", "AGPL-3.0"]
STALE_DAYS = 180  # repos with no commit in 6 months
MIN_STARS = 10    # ignore noise

# Verdict labels
VERDICTS = ["INTEGRATE", "ADAPT", "WATCH", "SKIP", "DANGEROUS"]


# ── GITHUB API ────────────────────────────────────────────────────────────────

def search_repos_by_topic(topic: str, limit: int = 10) -> list[dict]:
    """Search GitHub repos by topic, sorted by recently updated."""
    url = "https://api.github.com/search/repositories"
    params = {
        "q": f"topic:{topic} language:python language:javascript language:typescript",
        "sort": "updated",
        "order": "desc",
        "per_page": min(limit, 30),
    }
    try:
        r = requests.get(url, headers=GITHUB_HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as e:
        print(f"  [WARN] Topic {topic} search failed: {e}")
        return []


def get_repo_readme(owner: str, repo: str) -> str:
    """Fetch README content (truncated to 3000 chars for scoring)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        r = requests.get(url, headers={**GITHUB_HEADERS, "Accept": "application/vnd.github.raw"}, timeout=10)
        if r.status_code == 200:
            return r.text[:3000]
    except Exception:
        pass
    return ""


def get_repo_languages(owner: str, repo: str) -> dict:
    """Fetch language breakdown for a repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}/languages"
    try:
        r = requests.get(url, headers=GITHUB_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def get_security_advisories(owner: str, repo: str) -> int:
    """Return count of open security advisories (0 if API unavailable)."""
    url = f"https://api.github.com/repos/{owner}/{repo}/security-advisories"
    try:
        r = requests.get(url, headers=GITHUB_HEADERS, timeout=10)
        if r.status_code == 200:
            return len(r.json())
    except Exception:
        pass
    return 0


# ── SCORING ───────────────────────────────────────────────────────────────────

def pre_screen(repo: dict) -> tuple[bool, str]:
    """
    Fast pre-screen before sending to Claude.
    Returns (passes, reason_if_failed).
    """
    # Stars floor
    if repo.get("stargazers_count", 0) < MIN_STARS:
        return False, f"Low signal ({repo.get('stargazers_count', 0)} stars)"

    # Stale check
    pushed = repo.get("pushed_at", "")
    if pushed:
        last = datetime.datetime.fromisoformat(pushed.replace("Z", "+00:00"))
        age = (datetime.datetime.now(datetime.timezone.utc) - last).days
        if age > STALE_DAYS:
            return False, f"Stale ({age} days since last commit)"

    # License check
    license_info = repo.get("license") or {}
    spdx = license_info.get("spdx_id", "")
    if spdx in BANNED_LICENSES:
        return False, f"Incompatible license ({spdx})"

    # Archived
    if repo.get("archived"):
        return False, "Archived repo"

    return True, ""


def score_with_claude(repo: dict, readme: str) -> dict:
    """Send repo metadata + README to Claude for the agent network fit scoring."""
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    license_info = repo.get("license") or {}
    prompt = f"""
You are the intelligence officer for the agent network trading and AI agent ecosystem.
Evaluate this GitHub repo and return ONLY a JSON object with this exact shape:

{{
  "verdict": "<INTEGRATE|ADAPT|WATCH|SKIP|DANGEROUS>",
  "network_fit": "<which component of your stack this plugs into, or NONE>",
  "summary": "<1-2 sentence plain-English summary of what this repo does>",
  "strengths": ["<up to 3 specific strengths relevant to the agent network>"],
  "risks": ["<up to 3 risks: technical debt, security, license, stale deps, etc>"],
  "integration_note": "<specific action to integrate — or why to skip>"
}}

Verdict guide:
- INTEGRATE: Works now, plugs directly into the stack
- ADAPT: Good core, needs modification for the agent network
- WATCH: Promising but immature or wrong language — monitor
- SKIP: Not relevant or too risky
- DANGEROUS: Security risk, malicious patterns, viral license

PROFILE:
{NETWORK_PROFILE}

REPO TO EVALUATE:
Name: {repo['full_name']}
Description: {repo.get('description', 'None')}
Stars: {repo.get('stargazers_count')} | Forks: {repo.get('forks_count')}
Language: {repo.get('language')}
License: {license_info.get('spdx_id', 'Unknown')}
Last pushed: {repo.get('pushed_at')}
Topics: {', '.join(repo.get('topics', []))}
Open issues: {repo.get('open_issues_count')}

README (first 3000 chars):
{readme or '(no README)'}

Return ONLY the JSON. No preamble, no markdown fences.
"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        text = msg.content[0].text.strip()
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "verdict": "SKIP",
            "network_fit": "NONE",
            "summary": "Scoring failed — JSON parse error.",
            "strengths": [],
            "risks": ["Claude returned malformed JSON"],
            "integration_note": "Re-run manually."
        }
    except Exception as e:
        return {
            "verdict": "SKIP",
            "network_fit": "NONE",
            "summary": f"Scoring failed: {e}",
            "strengths": [],
            "risks": [str(e)],
            "integration_note": "API error — retry."
        }


# ── OUTPUT ────────────────────────────────────────────────────────────────────

VERDICT_EMOJI = {
    "INTEGRATE": "🟢",
    "ADAPT": "🟡",
    "WATCH": "🔵",
    "SKIP": "⚫",
    "DANGEROUS": "🔴",
}

def format_result(repo: dict, score: dict) -> str:
    """Format a single repo result as Obsidian markdown."""
    emoji = VERDICT_EMOJI.get(score.get("verdict", "SKIP"), "⚫")
    stars = repo.get("stargazers_count", 0)
    forks = repo.get("forks_count", 0)
    pushed = repo.get("pushed_at", "")[:10]
    license_info = repo.get("license") or {}
    spdx = license_info.get("spdx_id", "Unknown")
    url = repo.get("html_url", "")
    strengths = "\n".join(f"  - {s}" for s in score.get("strengths", []))
    risks = "\n".join(f"  - {r}" for r in score.get("risks", []))

    return f"""
### {emoji} [{repo['full_name']}]({url})
**Verdict:** `{score.get('verdict')}` | **Network fit:** {score.get('network_fit', 'NONE')}
**Stars:** {stars:,} | **Forks:** {forks:,} | **Last Push:** {pushed} | **License:** {spdx}

> {score.get('summary', '')}

**Strengths:**
{strengths or '  - None noted'}

**Risks:**
{risks or '  - None noted'}

**Integration Note:** {score.get('integration_note', '')}

---"""


def build_digest(results: list[dict]) -> str:
    """Build full Obsidian markdown digest."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    integrate = [r for r in results if r["score"].get("verdict") == "INTEGRATE"]
    adapt = [r for r in results if r["score"].get("verdict") == "ADAPT"]
    watch = [r for r in results if r["score"].get("verdict") == "WATCH"]
    dangerous = [r for r in results if r["score"].get("verdict") == "DANGEROUS"]

    header = f"""# GitHub Intel Digest
**Generated:** {now}
**Repos scanned:** {len(results)}
**Integrate:** {len(integrate)} | **Adapt:** {len(adapt)} | **Watch:** {len(watch)} | **Dangerous:** {len(dangerous)}

---

## 🟢 INTEGRATE — Deploy Now
"""
    sections = header
    for r in integrate:
        sections += format_result(r["repo"], r["score"])

    sections += "\n## 🟡 ADAPT — Modify for the agent network\n"
    for r in adapt:
        sections += format_result(r["repo"], r["score"])

    sections += "\n## 🔵 WATCH — Monitor\n"
    for r in watch:
        sections += format_result(r["repo"], r["score"])

    if dangerous:
        sections += "\n## 🔴 DANGEROUS — Do Not Touch\n"
        for r in dangerous:
            sections += format_result(r["repo"], r["score"])

    return sections


def save_digest(content: str):
    """Save digest to Obsidian vault or stdout."""
    if OBSIDIAN_VAULT_PATH:
        folder = Path(OBSIDIAN_VAULT_PATH) / "agent-network" / "GitHub-Intel"
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"github-digest-{datetime.datetime.now().strftime('%Y-%m-%d')}.md"
        path = folder / filename
        path.write_text(content, encoding="utf-8")
        print(f"\n✅ Digest saved to: {path}")
    else:
        print(content)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run(topics: list[str], limit_per_topic: int = 5, dry_run: bool = False):
    seen_repos = set()
    results = []

    print(f"\n🔍 GitHub Scanner — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Topics: {', '.join(topics)}")
    print(f"Limit per topic: {limit_per_topic} | Dry run: {dry_run}\n")

    for topic in topics:
        print(f"📡 Scanning topic: {topic}")
        repos = search_repos_by_topic(topic, limit=limit_per_topic)
        time.sleep(1)  # GitHub rate limit courtesy

        for repo in repos:
            full_name = repo["full_name"]
            if full_name in seen_repos:
                continue
            seen_repos.add(full_name)

            # Pre-screen
            passes, reason = pre_screen(repo)
            if not passes:
                print(f"  ⚫ SKIP  {full_name} — {reason}")
                continue

            if dry_run:
                print(f"  ✔ PASS  {full_name} ({repo.get('stargazers_count')} ⭐)")
                continue

            # Fetch README
            owner, name = full_name.split("/")
            readme = get_repo_readme(owner, name)
            time.sleep(0.5)

            # Score with Claude
            print(f"  🤖 Scoring {full_name}...", end=" ")
            score = score_with_claude(repo, readme)
            verdict = score.get("verdict", "SKIP")
            emoji = VERDICT_EMOJI.get(verdict, "⚫")
            print(f"{emoji} {verdict}")
            time.sleep(1)  # Anthropic rate limit courtesy

            results.append({"repo": repo, "score": score})

    if not dry_run and results:
        digest = build_digest(results)
        save_digest(digest)
        print(f"\n📊 Scan complete. {len(results)} repos evaluated.")
    elif dry_run:
        print(f"\n[DRY RUN] Would evaluate {len(seen_repos)} repos that passed pre-screen.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GitHub Scanner")
    parser.add_argument("--topics", nargs="*", default=SCAN_TOPICS, help="GitHub topics to scan")
    parser.add_argument("--limit", type=int, default=5, help="Repos per topic")
    parser.add_argument("--dry-run", action="store_true", help="Pre-screen only, no Claude scoring")
    args = parser.parse_args()

    if not GITHUB_TOKEN:
        print("❌ GITHUB_TOKEN not set in .env")
        sys.exit(1)
    if not ANTHROPIC_API_KEY and not args.dry_run:
        print("❌ ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    run(topics=args.topics, limit_per_topic=args.limit, dry_run=args.dry_run)
