"""Archive/restore one WAL file; missing files return nonzero to PostgreSQL."""
from pathlib import Path
import shutil
import sys

try:
    source, destination = map(Path, sys.argv[1:3])
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
except OSError as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
