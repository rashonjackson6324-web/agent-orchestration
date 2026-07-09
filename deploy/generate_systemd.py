#!/usr/bin/env python3
"""
SYSTEMD GENERATOR
Generates all service + timer files for GCP host.
Run this ON THE host to create all systemd units.
GCP: python3 ~/.agent-network/generate_systemd.py && sudo python3 ~/.agent-network/generate_systemd.py --install
"""

import os, subprocess, sys

GCP_USER    = os.getenv("GCP_USER")
HOME        = f"/home/{GCP_USER}"
AGENTS_DIR  = f"{HOME}/agents"
NETWORK_DIR     = f"{HOME}/agent-network"
VENV        = f"{HOME}/agent-network/venv/bin/python"
SYSTEMD_DIR = "/etc/systemd/system"

SERVICES = {
    "domain_scorer": {
        "script":      f"{AGENTS_DIR}/domain_scorer/domain_scorer.py",
        "desc":        "Domain scorer - weighted daily composite",
        "timer_cal":   "*-*-* 07:00:00 America/New_York",
        "type":        "oneshot"
    },
    "trading_executor": {
        "script":      f"{AGENTS_DIR}/trading_executor/trading_executor.py",
        "desc":        "Trading executor - Alpaca, paper mode",
        "timer_cal":   "Mon..Fri *-*-* 09:30..16:00:00/900 America/New_York",
        "type":        "oneshot"
    },
    "task_watchdog": {
        "script":      f"{AGENTS_DIR}/task_watchdog/task_watchdog.py digest",
        "desc":        "Task watchdog - daily digest",
        "timer_cal":   "*-*-* 08:00:00 America/New_York",
        "type":        "oneshot"
    },
    "task_watchdog-watch": {
        "script":      f"{AGENTS_DIR}/task_watchdog/task_watchdog.py watchdog",
        "desc":        "Task watchdog - every 6h",
        "timer_cal":   "*-*-* 02,08,14,20:00:00",
        "type":        "oneshot"
    },
    "strategy_agent": {
        "script":      f"{AGENTS_DIR}/strategy_agent/strategy_agent.py",
        "desc":        "Strategy agent - daily directive",
        "timer_cal":   "*-*-* 08:00:00 America/New_York",
        "type":        "oneshot"
    },
    "output_validator": {
        "script":      f"{AGENTS_DIR}/output_validator/output_validator.py",
        "desc":        "Output validator - seven-point QC checklist",
        "timer_cal":   "*-*-* 18:00:00 America/New_York",
        "type":        "oneshot"
    },
    "repo_scanner": {
        "script":      f"{NETWORK_DIR}/repo_scanner.py",
        "desc":        "Repo scanner - GitHub intelligence",
        "timer_cal":   "*-*-* 06:00:00 America/New_York",
        "type":        "oneshot"
    },
    "health_monitor": {
        "script":      f"{NETWORK_DIR}/health_monitor.py",
        "desc":        "Health monitor - silent success, loud failure",
        "timer_cal":   "*:0/20",   # every 20 minutes
        "type":        "oneshot"
    },
    "youtube_ingestor": {
        "script":      f"{NETWORK_DIR}/youtube_ingestor.py",
        "desc":        "YouTube ingestor - weekly digest",
        "timer_cal":   "Wed *-*-* 13:00:00",
        "type":        "oneshot"
    }
}

SERVICE_TEMPLATE = """[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target

[Service]
Type={type}
User={user}
WorkingDirectory={home}
ExecStart={python} {script}
EnvironmentFile={home}/agent-network/.env
StandardOutput=journal
StandardError=journal
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
"""

TIMER_TEMPLATE = """[Unit]
Description=Timer for {desc}
Requires={name}.service

[Timer]
OnCalendar={timer_cal}
Persistent=true
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
"""

def generate_files():
    os.makedirs("./systemd_units", exist_ok=True)
    for name, cfg in SERVICES.items():
        svc_content = SERVICE_TEMPLATE.format(
            desc=cfg["desc"],
            type=cfg["type"],
            user=GCP_USER,
            home=HOME,
            python=VENV,
            script=cfg["script"]
        )
        timer_content = TIMER_TEMPLATE.format(
            desc=cfg["desc"],
            name=name,
            timer_cal=cfg["timer_cal"]
        )
        with open(f"./systemd_units/{name}.service", "w") as f:
            f.write(svc_content)
        with open(f"./systemd_units/{name}.timer", "w") as f:
            f.write(timer_content)
        print(f"  Generated: {name}.service + {name}.timer")

def install_units():
    """Must run as root."""
    units_dir = "./systemd_units"
    for fname in os.listdir(units_dir):
        src = os.path.join(units_dir, fname)
        dst = os.path.join(SYSTEMD_DIR, fname)
        subprocess.run(["cp", src, dst], check=True)
        print(f"  Installed: {dst}")

    subprocess.run(["systemctl", "daemon-reload"], check=True)
    print("  Daemon reloaded")

    for name in SERVICES:
        try:
            subprocess.run(["systemctl", "enable", "--now", f"{name}.timer"], check=True)
            print(f"  Enabled: {name}.timer")
        except Exception as e:
            print(f"  WARNING: {name}.timer — {e}")

if __name__ == "__main__":
    print("SYSTEMD GENERATOR")
    print("=" * 40)
    generate_files()
    print(f"\nGenerated {len(SERVICES)} service + timer pairs in ./systemd_units/")

    if "--install" in sys.argv:
        if os.geteuid() != 0:
            print("\nERROR: --install requires root. Run: sudo python3 generate_systemd.py --install")
            sys.exit(1)
        print("\nInstalling to /etc/systemd/system/ ...")
        install_units()
        print("\nAll timers enabled. the agent network is live.")
    else:
        print("\nTo install: sudo python3 generate_systemd.py --install")
        print("To verify:  systemctl list-timers | grep agent")
