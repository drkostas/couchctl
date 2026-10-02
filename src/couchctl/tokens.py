"""Pairing tokens, kept in a JSON file only the user can read.

A Samsung token lets its holder do anything the remote can do, so it is a secret. It is never
printed and the file is written with mode 600.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def read(path: Path, device: str) -> str | None:
    try:
        return (json.loads(path.read_text(encoding="utf-8")) or {}).get(device)
    except (OSError, ValueError):
        return None


def write(path: Path, device: str, token: str) -> None:
    try:
        held = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        held = {}
    held[device] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(held, f, indent=2)
    os.chmod(path, 0o600)


def forget(path: Path, device: str) -> bool:
    try:
        held = json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return False
    if device not in held:
        return False
    del held[device]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(held, f, indent=2)
    return True
