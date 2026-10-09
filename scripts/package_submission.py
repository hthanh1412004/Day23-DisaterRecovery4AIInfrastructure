"""Package lab source and real evidence; exclude local runtimes/caches/state."""
from pathlib import Path
import os
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "D23-NguyenHuuThanh-2A202602807"
DEST = ROOT / "output" / NAME
DEST.mkdir(parents=True, exist_ok=True)
selected = []
for item in ("LMS.md", "GUIDE.md", "README.md", "RUBRIC.md", "requirements.txt", "Makefile",
             "docker-compose.yml", ".gitignore"):
    selected.append(ROOT/item)
for directory in ("dr", "serving", "edge", "state", "chaos", "loadgen", "tools", "tests", "scripts", "reports", "bonus"):
    for folder, directories, files in os.walk(ROOT/directory):
        directories[:] = [d for d in directories if d not in ("__pycache__", ".terraform", "bin", "data", "region-a", "region-b", "_replica")]
        for name in files:
            path = Path(folder)/name
            if name == "active_region" or path.suffix == ".pyc" or name.startswith("_test"):
                continue
            selected.append(path)
for source in selected:
    target = DEST / source.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
zip_path = ROOT/"output"/(NAME+".zip")
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for source in sorted(selected):
        archive.write(source, arcname=str(Path(NAME)/source.relative_to(ROOT)))
with zipfile.ZipFile(zip_path) as archive:
    assert archive.testzip() is None
    assert f"{NAME}/dr/runbook.py" in archive.namelist()
print(f"Packaged {len(selected)} files: {zip_path}")
