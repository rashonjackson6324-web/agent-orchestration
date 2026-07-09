# agent-orchestration

A network of autonomous Python agents running on a hardened GCP instance under `systemd`, with a Windows-side knowledge and file-routing layer. Each agent owns one responsibility, runs on its own timer, and reports to a single notification channel.

The design goal: **a system that operates for weeks without human input, and only speaks when something is wrong.**

---

## Architecture

```
                        ┌──────────────────────────┐
                        │  Notification sink       │
                        │  (loud failure,          │
                        │   silent success)        │
                        └────────────▲─────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
┌───────┴────────┐        ┌──────────┴─────────┐       ┌──────────┴─────────┐
│  INTELLIGENCE  │        │    SUPERVISION     │       │   QUALITY CONTROL  │
│                │        │                    │       │                    │
│ domain_scorer  │        │ task_watchdog      │       │ confidence_gate    │
│ strategy_agent │        │ health_monitor     │       │ output_validator   │
│ repo_scanner   │        │ ops_orchestrator   │       │ verifier           │
│ intel_aggreg.  │        │                    │       │ autonomy_monitor   │
│                │        │                    │       │ agent_audit        │
└───────┬────────┘        └──────────┬─────────┘       └──────────┬─────────┘
        │                            │                            │
        └────────────────────────────┼────────────────────────────┘
                                     │
              ┌──────────────────────┴───────────────────────┐
              │  core/                                       │
              │    executor · action_queue                   │
              │    deployment_agent · memory_sync            │
              └──────────────────────┬───────────────────────┘
                                     │
                ┌────────────────────┴────────────────────┐
                │                                         │
       ┌────────┴─────────┐                    ┌──────────┴─────────┐
       │  ingest/         │                    │  knowledge/        │
       │  github_scanner  │                    │  vault_manager     │
       │  youtube_ingestor│                    │  obsidian_connector│
       └──────────────────┘                    └────────────────────┘
```

---

## Agents

| Module | Responsibility | Cadence |
|---|---|---|
| `agents/domain_scorer.py` | Scores each configured domain 0–100 via LLM with live web grounding, weights them, emits a composite. | 07:00 ET |
| `agents/strategy_agent.py` | Consumes the composite score and emits one decisive directive: situation, priority, blocker, action. | 08:00 ET |
| `agents/output_validator.py` | Runs a seven-point checklist against the strategy agent's output before anything downstream trusts it. | 18:00 ET + webhook |
| `agents/task_watchdog.py` | SQLite task board, URL health checks, SSH liveness ping. Reconciles intended state against actual. | 08:00 ET + every 6h |
| `agents/health_monitor.py` | Two consecutive failures triggers an alert. Recovery triggers an alert. All clear is silence. | Every 20 min |
| `agents/repo_scanner.py` | Scans trending GitHub repos against a stack profile, scores relevance, summarizes. | 06:00 ET |
| `agents/intel_aggregator.py` | Reads every intel digest, extracts concrete actions, deduplicates, queues them. | After each digest |
| `agents/ops_orchestrator.py` | Condition-triggered ops layer. Watches inboxes and thresholds, queues work when a condition fires. | Every 5 min |

### The QC layer

`qc/confidence_gate.py` sits between every agent output and every downstream action:

| Score | Action |
|---|---|
| ≥ 70% | Pass — act on it |
| 50–69% | Flag — surface for review, do not act |
| < 50% | **Hard stop** — discard, alert |

This exists because LLM-driven agents are confidently wrong in ways deterministic code is not. Treating skepticism as a pipeline stage rather than a human review step is what makes unattended operation defensible.

`agents/output_validator.py` extends this into a structured seven-point checklist — every claim must be verifiable, consistent with prior state, correctly formatted for its destination, and free of undisclosed risk. Any point below the floor fails the whole output.

`qc/autonomy_monitor.py` guards against a subtler failure: the system quietly degrading from autonomous operation back into manual command execution. It checks that the agents are actually doing the work.

---

## Core

| Module | Purpose |
|---|---|
| `core/executor.py` | Command execution surface. Argv-only, never `shell=True`; commands must match an allow-list and scripts must resolve inside `SCRIPT_ROOT`. |
| `core/action_queue.py` | Typed task queue with pluggable executors per task type. |
| `core/deployment_agent.py` | Single funnel for all builds and deploys. |
| `core/memory_sync.py` | Aggregates every agent's state into one persistent, searchable memory file. |
| `deploy/generate_systemd.py` | Emits `.service` and `.timer` units from the agent manifest — no hand-written unit files. |

## Ingest & knowledge

`ingest/github_scanner.py` and `ingest/youtube_ingestor.py` pull external signal and reduce it to digests. `knowledge/vault_manager.py` and `knowledge/obsidian_connector.py` keep a Markdown knowledge base indexed and queryable, so a note written on the Windows side is retrievable by an agent on GCP within the sync window.

`windows/auto_organizer.ps1` routes inbound files by keyword. `windows/register_vault_tasks.ps1` registers the Task Scheduler jobs.

---

## Configuration, not code

Five things that look like source are actually private business context, so they live outside the repo:

| Config | Consumed by | Template |
|---|---|---|
| `config/domains.json` | `domain_scorer` | `config/domains.example.json` |
| `config/strategy_prompt.md` | `strategy_agent` | `config/strategy_prompt.example.md` |
| `config/stack_profile.md` | `github_scanner` | `config/stack_profile.example.md` |
| `config/client_brief_prompt.md` | `ops_orchestrator` | `config/client_brief_prompt.example.md` |
| `config/outreach_prompt.md` | `ops_orchestrator` | `config/outreach_prompt.example.md` |

All five real files are gitignored. The scorer validates that domain weights sum to `1.0` at load time and refuses to start otherwise — a misconfigured weighting would silently skew every composite it ever produced.

---

## Design principles

**One responsibility per agent.** An agent that scores signals does not also send alerts. Composition beats a monolith when you need to reason about which timer fired.

**Silent success.** Health checks that report "OK" every twenty minutes train you to ignore them. These say nothing until they have bad news.

**Systemd over cron.** Timers give you `systemctl status`, dependency ordering, restart policy, and journal integration. Cron gives you a mystery.

**Generated unit files.** Hand-maintained systemd units drift from the code they run. `generate_systemd.py` makes the manifest the single source of truth.

**An allow-list is theatre unless you drop the shell.** `core/executor.py` runs queued commands. With `shell=True`, an allow-list on the first token passes `systemctl status x; rm -rf /` — the check reads `systemctl` and waves it through. So the executor rejects shell metacharacters outright, splits with `shlex`, and runs the argv form. Script tasks must resolve inside `SCRIPT_ROOT`; a `script_path` of `../../etc/x` is refused. A task queue that accepts pipes and semicolons is a remote shell wearing a costume.

**Env-only credentials.** Every agent reads from the environment. No fallback literals, no committed `.env`. See `.env.example`.

**Prompts are configuration.** An LLM prompt that encodes your organization's private state is data, not code. Keeping it in a gitignored file means the repo can be public and the operation stays private.

---

## Running

```bash
cp .env.example .env                                   # fill in your own keys
cp config/domains.example.json config/domains.json     # define your domains
cp config/strategy_prompt.example.md config/strategy_prompt.md
pip install -r requirements.txt

python agents/domain_scorer.py     # run one agent directly

python deploy/generate_systemd.py --install
sudo systemctl daemon-reload && sudo systemctl enable --now domain_scorer.timer
```

## License

MIT
