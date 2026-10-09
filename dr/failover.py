"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """TODO: append 1 dòng JSONL có ts + iso vào LOG, và print ra stdout."""
    rec = dict(ts=time.time(), iso=datetime.now(timezone.utc).isoformat(), **kw)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))
    return rec


def state_of(region):
    response = httpx.get(f"{URL[region]}/v1/state", timeout=2)
    response.raise_for_status()
    return response.json()


def failover(target: str, backend: str, wait: float) -> dict:
    """TODO: 5 bước ở trên, đúng thứ tự."""
    if target not in URL or backend not in ("fs", "minio") or wait <= 0:
        raise ValueError("invalid target/backend/wait")
    completed = []
    current = "1_verify_target"
    result = dict(ok=False, target=target, steps=completed)
    try:
        initial = state_of(target)
        emit(step=current, target=target, state=initial, ok=True)
        completed.append(current)
        current = "2_restore_snapshot"
        started = time.monotonic()
        meta = snapshot.get(target, backend)
        source = meta.get("source_region", "a" if target == "b" else "b")
        rpo = snapshot.rpo(pathlib.Path(f"state/region-{source}/vectors.sqlite"),
                           pathlib.Path(f"state/region-{target}/vectors.sqlite"))
        result.update(meta, **rpo)
        emit(step=current, target=target, ok=True, duration_s=time.monotonic()-started,
             **meta, **rpo)
        completed.append(current)
        current = "3_scale_pool"
        pathlib.Path(f"state/region-{target}/pool_state").write_text("full", encoding="utf-8")
        emit(step=current, target=target, ok=True)
        completed.append(current)
        current = "4_wait_ready"
        started = time.monotonic()
        deadline = started + wait
        ready = False
        while time.monotonic() < deadline:
            try:
                ready = httpx.get(f"{URL[target]}/readyz",
                                  timeout=min(2, max(.001, deadline-time.monotonic()))).status_code == 200
            except httpx.RequestError:
                ready = False
            if ready:
                break
            time.sleep(min(.25, max(0, deadline-time.monotonic())))
        emit(step=current, target=target, ok=ready, waited_s=time.monotonic()-started)
        if not ready:
            return dict(result, error="target_readiness_timeout")
        completed.append(current)
        result["state"] = state_of(target)
        current = "5_dns_cutover"
        active = pathlib.Path("edge/active_region")
        temporary = active.with_suffix(".tmp")
        temporary.write_text(target, encoding="utf-8")
        temporary.replace(active)
        emit(step=current, target=target, ok=True)
        completed.append(current)
        return dict(result, ok=True)
    except (Exception, SystemExit) as exc:
        emit(step=current, target=target, ok=False, error=str(exc))
        return dict(result, error=str(exc))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
