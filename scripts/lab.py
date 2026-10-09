"""Run real local DR drills. All service processes are owned by this runner."""
import argparse
import json
import os
from pathlib import Path
import random
import socket
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from chaos import kill_region as chaos
from state.seed_vectors import seed
from tools.measure_rto import measure
import httpx
PROCESSES = []


def launch(args, name, env=None):
    Path("run").mkdir(exist_ok=True)
    with open(f"run/{name}.log", "w", encoding="utf-8") as log:
        # Windows venv python.exe is a redirector spawning another process.
        # Launch the actual interpreter so the PID belongs to uvicorn itself.
        child_env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path), **(env or {}))
        child = subprocess.Popen([sys._base_executable, *args], cwd=ROOT,
            env=child_env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    PROCESSES.append(child)
    return child


def wait_http(port, path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=1)
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    return {"status_code": response.status_code}
        except httpx.RequestError:
            pass
        time.sleep(.25)
    raise RuntimeError(f"service {port}{path} failed; inspect run logs")


def setup():
    for port in (8001, 8002, 8080):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"port {port} occupied; refusing to stop unknown services")
    for region in "ab":
        folder = ROOT / "state" / f"region-{region}"
        for relative in ("vectors.sqlite", "weights/model.bin", "weights/VERSION"):
            (folder / relative).unlink(missing_ok=True)
        seed(region, 200 if region == "a" else 0, 2 if region == "a" else 0)
    Path("edge/active_region").write_text("a", encoding="utf-8")
    for region, port in (("a", 8001), ("b", 8002)):
        child = launch(["-m", "uvicorn", "serving.app:app", "--host", "127.0.0.1",
                        "--port", str(port), "--log-level", "warning"], f"region-{region}",
                       {"REGION": region, "STATE_DIR": f"state/region-{region}", "WARMUP_SECONDS": "6"})
        Path(f"run/region-{region}.pid").write_text(str(child.pid))
        wait_http(port, "/healthz")
        httpx.get(f"http://127.0.0.1:{port}/v1/state", timeout=2)
    child = launch(["-m", "uvicorn", "edge.proxy:app", "--host", "127.0.0.1",
                    "--port", "8080", "--log-level", "warning"], "edge")
    Path("run/edge.pid").write_text(str(child.pid))
    wait_http(8080, "/edge/state")
    wait_http(8080, "/v1/infer")


def cleanup():
    for child in reversed(PROCESSES):
        if child.poll() is None:
            if os.name == "nt":
                chaos.windows_process(child.pid, "resume")
            child.terminate()
            child.wait(timeout=10)


def measured(path, health="reports/health-events.jsonl", failover="reports/failover-events.jsonl"):
    return measure(path, "chaos/chaos-events.jsonl", health, failover, 300)


def core():
    setup()
    baseline = launch(["loadgen/traffic.py", "--duration", "40", "--rps", "2",
                       "--out", "reports/drill-1-nodr.jsonl"], "baseline")
    time.sleep(8)
    chaos.kill("a", "netblock", "bare", False, True)
    baseline.wait(timeout=50)
    result = measured("reports/drill-1-nodr.jsonl")
    Path("reports/measure-drill-1.json").write_text(json.dumps(result, indent=2))
    print("BASELINE", json.dumps(result), flush=True)
    assert result["rto_verdict"] == "NO_RECOVERY"
    chaos.restore("a", "bare")
    wait_http(8001, "/readyz")
    ingest = launch(["state/ingest.py", "--region", "a", "--rate", ".5", "--duration", "150"], "ingest")
    replicate = launch(["state/replicate.py", "--every", "30", "--duration", "150", "--backend", "fs"], "replicate")
    time.sleep(5)
    traffic = launch(["loadgen/traffic.py", "--duration", "100", "--rps", "2",
                      "--out", "reports/drill-2-withdr.jsonl"], "drill2-traffic")
    health = launch(["dr/health_checker.py", "--interval", "5", "--threshold", "3",
                     "--duration", "100", "--out", "reports/health-events.jsonl"], "drill2-health")
    time.sleep(12)
    chaos.kill("a", "netblock", "bare", False, True)
    runbook = launch(["dr/runbook.py", "--primary", "a", "--target", "b", "--backend", "fs", "--auto"], "drill2-runbook")
    runbook.wait(timeout=90)
    traffic.wait(timeout=110)
    health.wait(timeout=110)
    result = measured("reports/drill-2-withdr.jsonl")
    Path("reports/measure-drill-2.json").write_text(json.dumps(result, indent=2))
    print("WITH_DR", json.dumps(result), flush=True)
    assert result["valid"] and not result["warnings"] and result["rto_verdict"] == "PASS"
    ingest.wait(timeout=60)
    replicate.wait(timeout=60)
    chaos.restore("a", "bare")


def randomized():
    from dr import failover, runbook
    from state import snapshot
    rng, summary = random.Random(23), []
    campaign = str(int(time.time()))
    for i in range(1, 6):
        setup()
        folder = Path(f"reports/bonus/campaign-{campaign}/chaos-{i}")
        folder.mkdir(parents=True, exist_ok=True)
        mode, delay = rng.choice(["stop", "netblock"]), rng.uniform(6, 12)
        snapshot.put("a", "fs")
        traffic_path, health_path = folder / "traffic.jsonl", folder / "health.jsonl"
        failover.LOG, runbook.LOG = folder / "failover.jsonl", folder / "runbook.jsonl"
        traffic = launch(["loadgen/traffic.py", "--duration", "80", "--rps", "2",
                          "--out", str(traffic_path)], f"bonus-{i}-traffic")
        health = launch(["dr/health_checker.py", "--interval", "5", "--threshold", "3",
                         "--duration", "80", "--out", str(health_path)], f"bonus-{i}-health")
        time.sleep(delay)
        deadline = time.monotonic() + 35
        chaos.kill("a", mode, "bare", False, True)
        while time.monotonic() < deadline:
            events = [json.loads(l) for l in health_path.read_text().splitlines()] if health_path.exists() else []
            if any(e.get("region") == "a" and e.get("to") == "UNHEALTHY" for e in events):
                break
            time.sleep(.25)
        assert runbook.run("a", "b", "fs", True)["ok"]
        traffic.wait(timeout=95)
        health.wait(timeout=95)
        result = measured(traffic_path, health_path, failover.LOG)
        assert result["valid"] and not result["warnings"]
        (folder / "measure.json").write_text(json.dumps(result, indent=2))
        summary.append(dict(run=i, kill_delay_s=delay, **result))
        cleanup()
        print(f"BONUS run {i}: {result['rto_measured_s']}s ({mode})", flush=True)
    rtos = [r["rto_measured_s"] for r in summary]
    Path("reports/bonus/randomized-summary.json").write_text(json.dumps(dict(
        seed=23, runs=summary, mean_rto_s=statistics.mean(rtos),
        sample_stddev_s=statistics.stdev(rtos)), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["core", "randomized"])
    args = parser.parse_args()
    try:
        core() if args.mode == "core" else randomized()
    finally:
        cleanup()
