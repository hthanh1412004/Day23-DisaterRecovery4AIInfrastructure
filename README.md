# Day 23 — Disaster Recovery & High Availability for AI Infrastructure

**Lab format:** 2 hours · **Environment:** fully local, no cloud account required · **Core idea:** kill your own region first, then learn to survive it.

> RTO/RPO are numbers you *measure* from logs — not numbers you read off a slide.

---

## Overview

This lab simulates a two-region AI serving deployment entirely on your own machine:

| Component | Real-world equivalent | Local stand-in |
|---|---|---|
| Region A / Region B | AWS us-east-1 / us-west-2 | Two FastAPI processes on different ports |
| Vector DB | Pinecone / Weaviate / Qdrant | SQLite file per region |
| Model weight replication | S3 Cross-Region Replication | Filesystem snapshot (`state/_replica/`) |
| DNS / Global Load Balancer | Route53 health-check failover | A local proxy reading a `edge/active_region` pointer file |
| Region outage | Network partition / AZ failure | `chaos/kill_region.py` (`SIGSTOP` or `SIGKILL`) |

You will bring the stack up, watch it serve traffic normally, **kill Region A while it is live**, and measure — with real timestamps, not intuition — how long it takes users to notice and how long it takes to recover. Then you build the health check, failover, and runbook automation that turns "no recovery" into a measured, passing RTO.

No AWS account, no cloud spend, no Docker required for the graded path.

## Quick Start

```bash
pip install -r requirements.txt
make seed              # seed Region A (200 docs + weights), Region B empty
bash scripts/up_bare.sh
curl localhost:8080/v1/infer
```

Expected response: `"edge_region":"a"` and an answer starting with `"[a] ..."`.

Stop everything:

```bash
bash scripts/down_bare.sh
```

> **Docker mode** (optional, `docker compose up -d`) works for local exploration, but is **not** used for the graded drill — timing is not reproducible across machines. Every drill in [GUIDE.md](GUIDE.md) runs in bare mode with `--mock`.

## Repository Structure

```
serving/    Mock inference API per region (FastAPI). Readiness depends on pool state,
            model weights on disk, and vector count — not just "process is alive".
edge/       DNS/load-balancer stand-in. Routes by reading edge/active_region,
            cached for EDGE_TTL_SECONDS to simulate real DNS cache behavior.
state/      Seed, ingest, snapshot/restore, and replication for the vector DB
            and model weights.
chaos/      kill_region.py — induces `stop` (SIGKILL) or `netblock` (SIGSTOP)
            failure, with a --mock flag for reproducible grading.
loadgen/    traffic.py — continuous request generator. This is the RTO clock:
            every request is one timestamped line in a JSONL log.
dr/         ★ YOUR ASSIGNMENT — three skeletons to implement:
            health_checker.py, failover.py, runbook.py
tools/      measure_rto.py — computes RTO/RPO from logs and validates the drill.
tests/      Unit tests for your dr/ code, plus the evidence-gate tests used
            for grading.
reports/    Templates you fill in: runbook.md, rto-evidence.md, postmortem.md.
scripts/    up_bare.sh / down_bare.sh — start/stop the stack without Docker.
```

## Completed submission — Nguyễn Hữu Thành (2A202602807)

Core implementation is complete in `dr/`. Real baseline: `NO_RECOVERY`.
Measured DR drill: **RTO 37.7s, RPO 14.02s / 7 documents**, recovered by Region B,
valid with no warnings. See [evidence](reports/rto-evidence.md),
[runbook](reports/runbook.md), [postmortem](reports/postmortem.md), and
[all six bonus tasks](reports/bonus.md).

Windows verification (Python 3.11 virtualenv; UTF-8 is required because the starter
evidence tests read Vietnamese Markdown using the default system encoding):

```powershell
.\.venv311\Scripts\python.exe -X utf8 -m pytest tests/ -v
```

The 13 original tests and 3 additional safety tests pass. Original grading tests,
serving API, edge proxy and measurement tool are unchanged. Unit tests run in a
temporary directory to avoid appending mocked events to drill evidence.

Reproduce core drills (resets generated lab state; ports 8001/8002/8080 must be free):

```powershell
.\.venv311\Scripts\python.exe -X utf8 scripts/lab.py core
.\.venv311\Scripts\python.exe -X utf8 scripts/write_reports.py
```

The runner owns and cleans up the service processes it launches. Windows chaos
uses native process suspend/resume instead of POSIX signals, and records the real
interpreter PID instead of the virtualenv launcher PID. This is a portability fix;
the readiness and warm-up behavior is preserved.

Bonus entry points: `scripts/lab.py randomized`, `bonus/verify_active_active.py`,
`bonus/pitr.py`, `bonus/minio_drill.py`. Run service-based demos sequentially since
they share ports. Runtime binaries and caches live under ignored `bonus/bin` and
`bonus/data`; they are excluded from the submission.

To reproduce PostgreSQL on Windows, `python scripts/fetch_portable.py` downloads
the official portable runtime with HTTP Range and checks ZIP CRCs. For MinIO,
`python scripts/fetch_go.py` downloads Go 1.24.2 and validates its official SHA256.
Then build the pinned source tag inside the workspace:

```powershell
$env:GOBIN = Join-Path (Get-Location) 'bonus\bin'
$env:GOPATH = Join-Path (Get-Location) 'bonus\data\gopath'
$env:GOCACHE = Join-Path (Get-Location) 'bonus\data\gocache'
$env:GOTOOLCHAIN = 'local'
$env:CGO_ENABLED = '0'
.\bonus\bin\go\bin\go.exe install github.com/minio/minio@RELEASE.2025-09-07T16-13-09Z
```

Terraform is write-only; only `init -backend=false`, `fmt -check`, and `validate`
were run. No AWS resources were created. Package source plus evidence with
`python scripts/package_submission.py`; the output is
`output/D23-NguyenHuuThanh-2A202602807.zip`.

## Lab references

| Document | Purpose |
|---|---|
| **[GUIDE.md](GUIDE.md)** | Full 2-hour walkthrough — setup, baseline, attack, containment, proof |
| **[RUBRIC.md](RUBRIC.md)** | Grading criteria, hard-fail conditions, and exact verification commands |

## Safety Notes

- Every target in this lab is `127.0.0.1` / `localhost`. Do not repoint `URL`, `UPSTREAM`, or the load generator at any external host.
- `--mock` mode only sends process signals (`SIGSTOP`/`SIGKILL`) and touches local files — nothing here reaches a real cloud region.
- The chaos script refuses to kill a region if the other one is already down, to prevent an accidental double outage. Overriding this (`--i-really-want-both`) marks the drill invalid for grading.
