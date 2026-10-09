"""Compare real MinIO and filesystem snapshots, then perform a MinIO failover."""
import json
import hashlib
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab
from state import snapshot
from dr import failover
import httpx

server = None
try:
    for port in (9000, 9001):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"port {port} already occupied; refusing to use an unknown MinIO server")
    Path("bonus/data/minio").mkdir(parents=True, exist_ok=True)
    with open("run/minio.log", "w") as log:
        server = subprocess.Popen([str(Path("bonus/bin/minio.exe").resolve()), "server", "bonus/data/minio",
            "--address", "127.0.0.1:9000", "--console-address", "127.0.0.1:9001"],
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    lab.wait_http(9000, "/minio/health/live")
    client = snapshot._s3()
    existing = [b["Name"] for b in client.list_buckets()["Buckets"]]
    if snapshot.BUCKET not in existing:
        client.create_bucket(Bucket=snapshot.BUCKET)
    lab.setup()
    records = []
    for backend in ("fs", "minio"):
        for i in range(3):
            t = time.monotonic()
            put = snapshot.put("a", backend)
            put_s = time.monotonic()-t
            t = time.monotonic()
            got = snapshot.get("b", backend)
            get_s = time.monotonic()-t
            assert got["embed_model_version"] == put["embed_model_version"]
            hashes = {r: hashlib.sha256(Path(f"state/region-{r}/vectors.sqlite").read_bytes()).hexdigest() for r in "ab"}
            assert hashes["a"] == hashes["b"]
            records.append(dict(ts=time.time(), backend=backend, repetition=i+1,
                                put_s=put_s, get_s=get_s, db_sha256=hashes["a"], manifest=got))
    failover.LOG = Path("reports/bonus/minio-failover.jsonl")
    result = failover.failover("b", "minio", 60)
    assert result["ok"]
    time.sleep(6)
    reply = lab.wait_http(8080, "/v1/infer")
    assert reply["region"] == "b"
    Path("reports/bonus/minio-summary.json").write_text(json.dumps(dict(
        timings=records, failover=result, inference=reply), indent=2))
    print("MINIO PASS", flush=True)
finally:
    lab.cleanup()
    if server and server.poll() is None:
        server.terminate()
        server.wait(timeout=10)
