"""Safety properties absent from the starter tests."""
import json
from pathlib import Path

from dr import failover, runbook


def test_runbook_calls_failover_once_and_reuses_result(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("edge").mkdir()
    Path("edge/active_region").write_text("b")
    monkeypatch.setattr(runbook, "LOG", Path("runbook.jsonl"))
    monkeypatch.setattr(runbook.time, "sleep", lambda _: None)
    monkeypatch.setattr(runbook.hc, "probe", lambda *_: (False, "timeout"))
    class Response:
        status_code = 200
        def json(self):
            return {"region": "b"}
    monkeypatch.setattr(runbook.httpx, "get", lambda *a, **kw: Response())
    monkeypatch.setattr(runbook.httpx.Client, "get", lambda *a, **kw: Response())
    calls = []
    def recover(*args, **kw):
        calls.append(args)
        return dict(ok=True, state=dict(count=200, weights=True), rpo_seconds=2, docs_lost=1)
    monkeypatch.setattr(runbook.fo, "failover", recover)
    assert runbook.run("a", "b", "fs", True)["ok"]
    assert len(calls) == 1
    rows = [json.loads(l) for l in Path("runbook.jsonl").read_text().splitlines()]
    assert [r["step"] for r in rows] == list(range(1, 8))
    assert rows[5]["requests"] == 10


def test_declined_confirmation_never_fails_over(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runbook, "LOG", Path("runbook.jsonl"))
    monkeypatch.setattr(runbook.time, "sleep", lambda _: None)
    monkeypatch.setattr(runbook.hc, "probe", lambda *_: (False, "timeout"))
    monkeypatch.setattr(runbook.httpx, "get", lambda *a, **kw: type("R", (), {"status_code": 200})())
    monkeypatch.setattr("builtins.input", lambda _: "n")
    monkeypatch.setattr(runbook.fo, "failover", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unsafe cutover")))
    assert runbook.run("a", "b", "fs", False)["error"] == "operator_declined"


def test_missing_snapshot_aborts_without_cutover(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path("edge").mkdir()
    Path("edge/active_region").write_text("a")
    monkeypatch.setattr(failover, "LOG", Path("failover.jsonl"))
    monkeypatch.setattr(failover, "state_of", lambda _: {"pool_state": "warm"})
    def missing(*a):
        raise SystemExit("no snapshot")
    monkeypatch.setattr(failover.snapshot, "get", missing)
    assert not failover.failover("b", "fs", 1)["ok"]
    assert Path("edge/active_region").read_text() == "a"
