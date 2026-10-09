"""Keep starter unit-test monkeypatch events out of real drill evidence."""
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolate_starter_unit_tests(request, monkeypatch, tmp_path):
    if request.node.path.name != "test_failover.py":
        return
    monkeypatch.chdir(tmp_path)
    Path("edge").mkdir()
    Path("edge/active_region").write_text("a")
    Path("state/region-b").mkdir(parents=True)
