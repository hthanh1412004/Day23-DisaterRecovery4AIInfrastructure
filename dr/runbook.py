"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time
import math
from datetime import datetime, timezone

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    record = dict(ts=time.time(), iso=datetime.now(timezone.utc).isoformat(),
                  step=n, name=name, **kw)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    print(json.dumps(record))
    return record


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        print(msg + " [--auto]")
        return True
    try:
        return input(msg + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    if primary not in URL or target not in URL or primary == target:
        raise ValueError("primary and target must be different local regions")
    started = time.monotonic()
    probes = []
    for i in range(3):
        p_ready, reason = hc.probe(primary, 2)
        try:
            target_alive = httpx.get(f"{URL[target]}/healthz", timeout=2).status_code == 200
        except httpx.RequestError:
            target_alive = False
        probes.append(dict(primary_ready=p_ready, reason=reason, target_alive=target_alive))
        if i < 2:
            time.sleep(5)
    outage = all(not p["primary_ready"] and p["target_alive"] for p in probes)
    step(1, "xac_nhan_outage", ok=outage, probes=probes)
    if not outage:
        return dict(ok=False, error="outage_not_confirmed_or_target_dead")
    kills = pathlib.Path("chaos/chaos-events.jsonl")
    events = [json.loads(l) for l in kills.read_text().splitlines()] if kills.exists() else []
    event = next((e for e in reversed(events) if e.get("action") == "kill"
                  and e.get("region") == primary), {})
    step(2, "thong_bao_incident", t_outage=event.get("ts"), primary=primary, target=target)
    if not confirm(auto, f"Confirm failover {primary} -> {target}?"):
        return dict(ok=False, error="operator_declined")
    result = fo.failover(target, backend, wait=60)
    step(3, "scale_gpu_pool", result=result, ok=result.get("ok", False))
    if not result.get("ok"):
        step(7, "post_incident", ok=False, elapsed_s=time.monotonic()-started,
             error=result.get("error"))
        return result
    replica = result.get("state", {})
    replica_ok = bool(replica.get("weights") and replica.get("count", 0) > 0)
    step(4, "verify_state_replica", ok=replica_ok, state=replica,
         rpo_seconds=result.get("rpo_seconds"), docs_lost=result.get("docs_lost"))
    cutover_ok = pathlib.Path("edge/active_region").read_text().strip() == target
    step(5, "dns_cutover", ok=cutover_ok, target=target)
    latencies, failures = [], 0
    with httpx.Client(timeout=3) as client:
        for _ in range(10):
            t0 = time.monotonic()
            try:
                response = client.get(f"{URL[target]}/v1/infer")
                good = response.status_code == 200 and response.json().get("region") == target
            except (httpx.RequestError, ValueError):
                good = False
            failures += not good
            latencies.append((time.monotonic()-t0)*1000)
    p95 = sorted(latencies)[math.ceil(.95*len(latencies))-1]
    signals_ok = failures == 0 and p95 < 1000
    step(6, "verify_golden_signals", ok=signals_ok, requests=10,
         p95_latency_ms=round(p95, 2), error_rate=failures/10)
    result["ok"] = replica_ok and cutover_ok and signals_ok
    step(7, "post_incident", ok=result["ok"], elapsed_s=time.monotonic()-started,
         measure_command="python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
