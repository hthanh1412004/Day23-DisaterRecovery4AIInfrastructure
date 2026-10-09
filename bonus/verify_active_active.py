"""Verify real 50/50 inference and deterministic concurrent conflict resolution."""
from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
import json
from pathlib import Path
import sys
import socket
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import lab
from bonus.active_active import Document, merge
import httpx

try:
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 8081)) == 0:
            raise RuntimeError("port 8081 already occupied; refusing to use an unknown edge")
    lab.setup()
    from state import snapshot
    snapshot.put("a", "fs")
    snapshot.get("b", "fs")
    Path("state/region-b/pool_state").write_text("full")
    lab.wait_http(8002, "/readyz")
    lab.launch(["-m", "uvicorn", "bonus.active_active:app", "--host", "127.0.0.1",
                "--port", "8081", "--log-level", "warning"], "active-active")
    lab.wait_http(8081, "/openapi.json")
    with httpx.Client(timeout=5) as client:
        replies = [client.get("http://127.0.0.1:8081/v1/infer").json() for _ in range(20)]
    counts = {r: sum(x.get("region") == r for x in replies) for r in "ab"}
    assert counts == {"a": 10, "b": 10}, counts
    versions = [dict(body="a-v1", logical_clock=1, region="a"),
                dict(body="a-v2", logical_clock=2, region="a"),
                dict(body="b-v2", logical_clock=2, region="b")]
    results = []
    for i, order in enumerate(permutations(versions)):
        for row in order:
            result = merge(Document(doc_id=f"order-{i}", **row))
        assert result["body"] == "b-v2"
        results.append(result)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda row: merge(Document(doc_id="concurrent", **row)), versions))
    winner = merge(Document(doc_id="concurrent", **versions[0]))
    assert winner["body"] == "b-v2"
    Path("reports/bonus").mkdir(parents=True, exist_ok=True)
    Path("reports/bonus/active-active.json").write_text(json.dumps(dict(
        counts=counts, requests=replies, permutations=results, concurrent_winner=winner), indent=2))
    print("ACTIVE_ACTIVE PASS", counts)
finally:
    lab.cleanup()
