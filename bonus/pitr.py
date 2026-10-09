"""Real pg_basebackup + WAL archiving + recovery_target_time drill on localhost."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
BIN = ROOT / "bonus/bin/pgsql/bin"
WORK = ROOT / "bonus/data" / f"pitr-{int(time.time())}"
PRIMARY, BASE, RESTORED, WAL = [WORK / n for n in ("primary", "base", "restored", "wal")]
LOG = ROOT / "reports/bonus/pitr-events.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)
WAL.mkdir(parents=True)
started_servers = []


def event(name, **fields):
    rec = dict(ts=time.time(), iso=datetime.now(timezone.utc).isoformat(), event=name, **fields)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)
    return rec


def command(name, *args):
    # pg_ctl start launches a background server; inherited pipe handles can
    # keep communicate() waiting for EOF even after pg_ctl itself has exited.
    trace = WORK / f"command-{time.time_ns()}-{name}.log"
    with trace.open("w", encoding="utf-8") as stream:
        output = subprocess.run([str(BIN / (name+".exe")), *map(str, args)],
            stdout=stream, stderr=subprocess.STDOUT, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    content = trace.read_text(encoding="utf-8", errors="replace").strip()
    if output.returncode:
        raise RuntimeError(content)
    return content


def sql(query, port=15432):
    return command("psql", "-h", "127.0.0.1", "-p", port, "-U", "postgres", "-d", "postgres",
                   "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", query)


def start(folder):
    command("pg_ctl", "-D", folder, "-l", folder.parent / (folder.name+".log"), "-w", "start")
    started_servers.append(folder)


def stop(folder):
    command("pg_ctl", "-D", folder, "-m", "fast", "-w", "stop")
    started_servers.remove(folder)


def setting(value):
    return "'" + str(value).replace("\\", "/").replace("'", "''") + "'"


try:
    command("initdb", "-D", PRIMARY, "-U", "postgres", "-A", "trust", "--encoding=UTF8", "--locale=C")
    python = Path(sys._base_executable).as_posix()
    helper = (ROOT / "bonus/copy_wal.py").as_posix()
    archive = f'"{python}" "{helper}" "%p" "{WAL.as_posix()}/%f"'
    with (PRIMARY / "postgresql.conf").open("a", encoding="utf-8") as cfg:
        cfg.write("\nlisten_addresses='127.0.0.1'\nport=15432\nwal_level=replica\narchive_mode=on\n")
        cfg.write("archive_command=" + setting(archive) + "\n")
    start(PRIMARY)
    sql("CREATE TABLE metadata(doc_id text PRIMARY KEY, body text, ingested_at timestamptz DEFAULT clock_timestamp());")
    sql("INSERT INTO metadata(doc_id,body) VALUES ('seed','base backup record');")
    t = time.monotonic()
    command("pg_basebackup", "-h", "127.0.0.1", "-p", "15432", "-U", "postgres", "-D", BASE,
            "-X", "stream", "--checkpoint=fast", "--no-password")
    event("basebackup_complete", duration_s=time.monotonic()-t)
    sql("INSERT INTO metadata(doc_id,body) VALUES ('keep','must survive PITR');")
    target = sql("SELECT clock_timestamp();")
    event("recovery_target", recovery_target_time=target)
    time.sleep(.5)
    sql("INSERT INTO metadata(doc_id,body) VALUES ('discard','committed after target');")
    event("primary_before_outage", rows=sql("SELECT doc_id FROM metadata ORDER BY doc_id;").splitlines())
    sql("SELECT pg_switch_wal();")
    deadline = time.monotonic()+60
    while time.monotonic() < deadline:
        archived = int(sql("SELECT archived_count FROM pg_stat_archiver;"))
        if archived > 0:
            break
        time.sleep(.5)
    assert archived > 0, "WAL archiving failed"
    event("wal_archived", files=[p.name for p in WAL.iterdir()])
    outage = event("metadata_outage")
    stop(PRIMARY)
    t = time.monotonic()
    shutil.copytree(BASE, RESTORED)
    restore = f'"{python}" "{helper}" "{WAL.as_posix()}/%f" "%p"'
    with (RESTORED / "postgresql.conf").open("a", encoding="utf-8") as cfg:
        cfg.write("\nport=15433\narchive_mode=off\nrestore_command="+setting(restore)+"\n")
        cfg.write("recovery_target_time="+setting(target)+"\nrecovery_target_action='promote'\n")
    (RESTORED / "recovery.signal").touch()
    start(RESTORED)
    # pg_ctl readiness includes hot-standby connections; wait for target replay
    # and promotion before treating the metadata service as writable/recovered.
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        if sql("SELECT pg_is_in_recovery();", 15433) == "f":
            break
        time.sleep(.25)
    rows = sql("SELECT doc_id FROM metadata ORDER BY doc_id;", 15433).splitlines()
    assert rows == ["keep", "seed"], rows
    assert sql("SELECT pg_is_in_recovery();", 15433) == "f"
    recovered = event("pitr_recovered", rows=rows, restore_duration_s=time.monotonic()-t,
                      rto_seconds=time.time()-outage["ts"], recovery_target_time=target)
    Path("reports/bonus/pitr-summary.json").write_text(json.dumps(recovered, indent=2))
finally:
    for folder in list(started_servers):
        stop(folder)
