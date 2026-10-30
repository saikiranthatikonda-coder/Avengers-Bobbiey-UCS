"""Crash-safe file writes for user data.

write_text() truncates the file first; if BUCS or the PC stops mid-write, the
file is left cut short and the next load falls back to defaults — silently
losing passwords, memory or notes. atomic_write_text writes a sibling temp
file, flushes it to disk, then swaps it in with os.replace (atomic on Windows
and POSIX), so a reader always sees either the old or the new file.
"""

import os
from pathlib import Path


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
