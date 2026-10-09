"""Download official Go portable archive with parallel validated ranges."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import zipfile
import httpx

filename = "go1.24.2.windows-amd64.zip"
metadata = httpx.get("https://go.dev/dl/?mode=json&include=all", timeout=60, follow_redirects=True).json()
file = next(f for release in metadata for f in release["files"] if f["filename"] == filename)
size = file["size"]
url = "https://dl.google.com/go/" + filename


def fetch(i):
    start, end = i*size//12, (i+1)*size//12-1
    response = httpx.get(url+f"?part={i}", headers={"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}, timeout=240)
    response.raise_for_status()
    assert response.status_code == 206 and len(response.content) == end-start+1, (response.status_code, len(response.content), response.headers)
    print(f"Go part {i+1}/12 ready", flush=True)
    return response.content


with ThreadPoolExecutor(max_workers=12) as pool:
    data = b"".join(pool.map(fetch, range(12)))
assert hashlib.sha256(data).hexdigest() == file["sha256"]
root = Path("bonus/bin").resolve()
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    for item in archive.infolist():
        target = (root / item.filename).resolve()
        comparison = Path(str(target).removeprefix("\\\\?\\"))
        if not comparison.is_relative_to(root):
            raise ValueError("Unsafe ZIP entry")
    archive.extractall(root)
print("Go portable verified and extracted", flush=True)
