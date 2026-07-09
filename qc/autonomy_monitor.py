#!/usr/bin/env python3
"""
AUTONOMY MONITOR

Guards against the system quietly degrading from autonomous operation back
into manual command execution. Verifies each required agent is alive and
reports what it is capable of doing without human intervention.
"""

import os
import sqlite3
import json
import subprocess
import requests
import sys
from pathlib import Path
from datetime import datetime

# ===== CONFIG =====
DB_PATH = Path(os.getenv("NETWORK_ROOT", "./data")) / "session_log.db"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Agents that must be running for the system to be considered autonomous
REQUIRED_AGENTS = {
    "auto_organizer.ps1":    "File organization watcher",
    "obsidian_connector.py": "Knowledge base RAG connector",
    "vault_manager.py":      "Knowledge base indexing",
    "deployment_agent.py":   "Build and deploy funnel",
}

# What each agent can do, so a human is never asked to run a manual command
AGENT_CAPABILITIES = {
    "obsidian_connector.py": [
        "Connect the knowledge base to the RAG index",
        "Upload vault documents",
        "Sync vault changes",
        "Manage index workspaces"
    ],
    "vault_manager.py": [
        "Organize vault folders",
        "Create index files",
        "Back up the vault",
        "Maintain vault structure"
    ],
    "deployment_agent.py": [
        "Deploy apps",
        "Configure services",
        "Run builds",
        "Restart services"
    ],
    "auto_organizer.ps1": [
        "Sort files by category",
        "Rename files with dates",
        "Update the file manifest",
        "Move files to the correct folders"
    ],
}

# ===== FUNCTIONS =====
def check_process_running(process_name):
    """Check whether a process is running. No shell: process_name is not trusted."""
    try:
        output = subprocess.check_output(
            ["tasklist"], stderr=subprocess.DEVNULL
        ).decode(errors="replace")
    except Exception:
        return False
    return process_name.lower() in output.lower()

def send_telegram(message):
    """Send message to Telegram"""
    try:
        url = f"{TELEGRAM_API}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
        return True
    except:
        return False

def verify_database():
    """Check if session database exists and is healthy"""
    try:
        if not DB_PATH.exists():
            return False, "Database not found"
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sessions")
        conn.close()
        return True, "Database healthy"
    except Exception as e:
        return False, str(e)

def check_anythingllm():
    """Check if AnythingLLM is running"""
    try:
        response = requests.get("http://localhost:3001", timeout=2)
        return response.status_code < 500
    except:
        return False

def check_obsidian_vault():
    """Check if Obsidian vault exists and has files"""
    vault_path = Path(os.getenv("VAULT_ROOT", "./vault"))
    if not vault_path.exists():
        return False
    md_files = list(vault_path.rglob("*.md"))
    return len(md_files) > 0

def generate_agent_status_report():
    """Generate comprehensive agent health report"""
    print("\n" + "=" * 70)
    print("CLAUDE AUTONOMY MONITOR — SESSION VERIFICATION")
    print("=" * 70)

    status = {
        "timestamp": datetime.now().isoformat(),
        "agents_running": {},
        "systems_healthy": {},
        "critical_issues": [],
        "warnings": []
    }

    # Check each required agent
    print("\n[AGENTS]")
    for agent, description in REQUIRED_AGENTS.items():
        running = check_process_running(agent.split('.')[0])
        status["agents_running"][agent] = running
        symbol = "✓" if running else "✗"
        print(f"{symbol} {agent:40} | {description}")
        if not running:
            status["critical_issues"].append(f"{agent} is NOT running")

    # Check system health
    print("\n[SYSTEMS]")

    db_ok, db_msg = verify_database()
    status["systems_healthy"]["database"] = db_ok
    print(f"{'✓' if db_ok else '✗'} Database: {db_msg}")
    if not db_ok:
        status["critical_issues"].append(f"Database error: {db_msg}")

    vault_ok = check_obsidian_vault()
    status["systems_healthy"]["obsidian_vault"] = vault_ok
    print(f"{'✓' if vault_ok else '✗'} Obsidian Vault: {'Ready' if vault_ok else 'NOT FOUND'}")
    if not vault_ok:
        status["warnings"].append("Obsidian vault not found or empty")

    anythingllm_ok = check_anythingllm()
    status["systems_healthy"]["anythingllm"] = anythingllm_ok
    print(f"{'✓' if anythingllm_ok else '⚠'} AnythingLLM: {'Online' if anythingllm_ok else 'OFFLINE/ERROR'}")
    if not anythingllm_ok:
        status["warnings"].append("Knowledge base index not responding")

    # Summary
    print("\n[SUMMARY]")
    all_running = all(status["agents_running"].values())
    all_systems_ok = all(status["systems_healthy"].values())

    if all_running and all_systems_ok:
        print("✓ ALL SYSTEMS NOMINAL — FULL AUTONOMOUS OPERATION")
        status["operational_mode"] = "FULL_AUTONOMOUS"
    elif all_running and not all_systems_ok:
        print("⚠ AGENTS RUNNING — SYSTEMS NEED REPAIR")
        print("  → Agents will auto-repair systems")
        status["operational_mode"] = "AUTONOMOUS_WITH_REPAIRS"
    else:
        print("✗ CRITICAL: Some agents not running")
        print("  → Restart missing agents immediately")
        status["operational_mode"] = "DEGRADED"

    # Critical issues
    if status["critical_issues"]:
        print("\n[CRITICAL ISSUES]")
        for issue in status["critical_issues"]:
            print(f"  ✗ {issue}")

    # Warnings
    if status["warnings"]:
        print("\n[WARNINGS]")
        for warning in status["warnings"]:
            print(f"  ⚠ {warning}")

    # Agent capabilities reminder for Claude
    print("\n[CLAUDE REMINDER — DO NOT GIVE MANUAL COMMANDS]")
    print("Use these agents instead:")
    for agent, capabilities in AGENT_CAPABILITIES.items():
        if status["agents_running"].get(agent, False):
            print(f"\n  {agent}:")
            for cap in capabilities:
                print(f"    • {cap}")

    # Save report
    report_path = Path(os.getenv("NETWORK_ROOT", "./data")) / "autonomy_report.json"
    with open(report_path, "w") as f:
        json.dump(status, f, indent=2)

    print(f"\n[REPORT] Saved to: {report_path}")

    # Send to Telegram if any critical issues
    if status["critical_issues"]:
        msg = f"⚠️ CLAUDE AUTONOMY ALERT\n\n"
        for issue in status["critical_issues"]:
            msg += f"✗ {issue}\n"
        send_telegram(msg)

    print("\n" + "=" * 70)

    return status

# ===== MAIN =====
if __name__ == "__main__":
    status = generate_agent_status_report()

    # Exit with appropriate code
    if status["operational_mode"] == "FULL_AUTONOMOUS":
        sys.exit(0)  # All good
    elif status["operational_mode"] == "AUTONOMOUS_WITH_REPAIRS":
        sys.exit(1)  # Agents will fix
    else:
        sys.exit(2)  # Critical failure
