#!/usr/bin/env python3
import json
from pathlib import Path

AGENTS = {
    "domain_scorer.py":      {"role": "Daily multi-domain scorer",   "status": "DEPLOYED", "priority": "P0"},
    "strategy_agent.py":     {"role": "Daily directive",             "status": "DEPLOYED", "priority": "P0"},
    "output_validator.py":   {"role": "Seven-point output QC",       "status": "DEPLOYED", "priority": "P0"},
    "task_watchdog.py":      {"role": "Task board + health checks",  "status": "DEPLOYED", "priority": "P0"},
    "health_monitor.py":     {"role": "Service liveness",            "status": "DEPLOYED", "priority": "P0"},
    "repo_scanner.py":       {"role": "Repository intelligence",     "status": "DEPLOYED", "priority": "P2"},
    "intel_aggregator.py":   {"role": "Digest to action extraction", "status": "DEPLOYED", "priority": "P1"},
    "ops_orchestrator.py":   {"role": "Proactive trigger layer",     "status": "DEPLOYED", "priority": "P0"},
    "confidence_gate.py":    {"role": "Confidence threshold gate",   "status": "DEPLOYED", "priority": "P1"},
    "verifier.py":           {"role": "Output verification",         "status": "DEPLOYED", "priority": "P1"},
    "autonomy_monitor.py":   {"role": "Autonomy regression check",   "status": "DEPLOYED", "priority": "P1"},
    "action_queue.py":       {"role": "Typed task queue",            "status": "DEPLOYED", "priority": "P1"},
    "executor.py":           {"role": "Task execution engine",       "status": "DEPLOYED", "priority": "P0"},
    "deployment_agent.py":   {"role": "Build and deploy funnel",     "status": "DEPLOYED", "priority": "P0"},
    "memory_sync.py":        {"role": "Cross-agent memory",          "status": "DEPLOYED", "priority": "P1"},
    "vault_manager.py":      {"role": "Knowledge base indexing",     "status": "DEPLOYED", "priority": "P0"},
    "obsidian_connector.py": {"role": "Knowledge base RAG connector","status": "DEPLOYED", "priority": "P1"},
    "github_scanner.py":     {"role": "GitHub intel digest",         "status": "DEPLOYED", "priority": "P2"},
    "youtube_ingestor.py":   {"role": "YouTube digest",              "status": "DEPLOYED", "priority": "P2"},
}

print("\n" + "="*90)
print("the agent network — AGENT AUDIT")
print("="*90)

running = {k: v for k, v in AGENTS.items() if v["status"] == "RUNNING"}
deployed = {k: v for k, v in AGENTS.items() if v["status"] == "DEPLOYED"}
unknown = {k: v for k, v in AGENTS.items() if v["status"] == "UNKNOWN"}

print(f"\nSUMMARY: {len(AGENTS)} total agents")
print(f"  ✓ Running: {len(running)}")
print(f"  ✓ Deployed: {len(deployed)}")
print(f"  ? Unknown: {len(unknown)}")

print(f"\n{'='*90}")
print("RUNNING (24/7)")
print(f"{'='*90}")
for name, info in running.items():
    print(f"✓ {name:30} | {info['role']}")

print(f"\n{'='*90}")
print("DEPLOYED (On-Demand)")
print(f"{'='*90}")
for name, info in deployed.items():
    print(f"✓ {name:30} | {info['role']}")

print(f"\n{'='*90}")
print("UNKNOWN (Need Verification)")
print(f"{'='*90}")
for name, info in unknown.items():
    print(f"? {name:30} | {info['role']}")

print(f"\n{'='*90}")
print("MASTER STARTUP PLAN")
print(f"{'='*90}")
print("""
P0_STARTUP (init):
  → qc/autonomy_monitor.py

P0_CORE (core continuous):
  → auto_organizer.ps1 (file routing)

P0_EXECUTOR (task engine):
  → executor.py (task queue processor)

P0_OPS (operations):
  → ops_orchestrator.py (operations lead - coordinates all)

P1_AGENTS (support):
  → action_queue.py (content publishing)
  → obsidian_connector.py (RAG integration)
  → qc/autonomy_monitor.py (compliance checking)
  → confidence_gate.py (QC/hallucination detection)
  → verifier.py (output verification)

P2_SCHEDULED (timer-based):

RECOMMENDATION: Start P0 agents in background, let ops_orchestrator coordinate others
""")

print("\n" + "="*90)
print("ACTION REQUIRED")
print("="*90)
print("""
1. VERIFY 9 UNKNOWN AGENTS
   Are they production-ready? Should they auto-start?

2. CREATE STARTUP CONTROLLER
   Single PowerShell script that launches all agents in correct order

3. VERIFY DEPENDENCIES
   Which agents depend on others? What's the startup sequence?

Answer: Should I create master startup script now? (Yes/No)
""")
