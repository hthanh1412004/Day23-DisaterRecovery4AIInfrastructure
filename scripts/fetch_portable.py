"""Extract only PostgreSQL runtime files from official ZIP using HTTP ranges."""
import io
import json
from pathlib import Path
import struct
from concurrent.futures import ThreadPoolExecutor
import zlib
import zipfile
import time
import httpx

URL = "https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip"
OUT = Path("bonus/bin")


def get_range(lo, hi):
    for attempt in range(4):
        try:
            response = httpx.get(URL, headers={"Range": f"bytes={lo}-{hi}", "Accept-Encoding": "identity"}, timeout=120, follow_redirects=True)
            response.raise_for_status()
            if response.status_code != 206:
                raise RuntimeError("Server does not honor ranges")
            return response.content
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(1+attempt)


size = int(httpx.head(URL, timeout=30).headers["Content-Length"])
tail = get_range(size - 65536, size - 1)
pos = tail.rfind(b"PK\x05\x06")
end = tail[pos:pos+22]
fields = struct.unpack("<4s4H2LH", end)
central_size, central_offset = fields[5:7]
central = get_range(central_offset, central_offset+central_size-1)
# Build a sparse in-memory view: zipfile needs only the central directory here.
fake = io.BytesIO(central + end)
with zipfile.ZipFile(fake) as archive:
    entries = archive.infolist()
    # offsets in fake are adjusted by zipfile; recover actual offsets.
    selected = [e for e in entries if e.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/"))
                and not e.filename.startswith("pgsql/share/locale/") and not e.is_dir()]
    for e in selected:
        e.header_offset += central_offset


def extract(entry, chunk, chunk_start):
    name = entry.filename
    target = (OUT / name).resolve()
    comparison = Path(str(target).removeprefix("\\\\?\\"))
    if not comparison.is_relative_to(OUT.resolve()):
        raise ValueError(f"Unsafe ZIP entry: {name!r}, target={target}, root={OUT.resolve()}")
    if target.exists():
        return
    offset = entry.header_offset-chunk_start
    header = chunk[offset:offset+30]
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    start = offset+30+name_len+extra_len
    data = chunk[start:start+entry.compress_size]
    plain = zlib.decompress(data, -15) if entry.compress_type == zipfile.ZIP_DEFLATED else data
    assert len(plain) == entry.file_size and zlib.crc32(plain) == entry.CRC
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(plain)


missing = [e for e in selected if not (OUT/e.filename).exists()]
groups = []
for entry in sorted(missing, key=lambda e: e.header_offset):
    end = min(size-1, entry.header_offset+30+len(entry.filename.encode())+65535+entry.compress_size)
    if groups and entry.header_offset-groups[-1][1] < 65536 and end-groups[-1][0] < 4*1024**2:
        groups[-1][1] = end
        groups[-1][2].append(entry)
    else:
        groups.append([entry.header_offset, end, [entry]])


def group_extract(group):
    start, end, entries = group
    data = get_range(start, end)
    for entry in entries:
        extract(entry, data, start)
    print(f"Extracted {len(entries)} runtime files", flush=True)


print(f"Fetching {len(missing)} missing files in {len(groups)} ranged blocks", flush=True)
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(group_extract, groups))
print("PostgreSQL runtime ready", flush=True)
