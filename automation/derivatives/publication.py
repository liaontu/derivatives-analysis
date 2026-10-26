"""Shared change accounting for published JSON and rendered files."""
from pathlib import Path

WRITTEN: set[Path] = set()


def save_text(path, text):
    data = text.encode('utf-8')
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    WRITTEN.add(path)
    return True
