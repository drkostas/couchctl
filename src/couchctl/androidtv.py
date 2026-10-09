"""Android TV boxes and projectors over adb.

adb must be installed, network debugging must be on in the device's developer options, and the
first connection shows a prompt on screen that somebody has to allow once.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping

from .config import Device


def adb_path(env: Mapping[str, str] | None = None) -> str | None:
    """COUCHCTL_ADB, or adb found on the PATH. With `env`, both are read from it."""
    src = os.environ if env is None else env
    return src.get("COUCHCTL_ADB") or shutil.which("adb", path=src.get("PATH"))


def shell(target: str, cmd: str, timeout: float = 8.0, *, env: Mapping[str, str] | None = None) -> str | None:
    """One adb shell command, or None if the device did not answer."""
    adb = adb_path(env)
    if not adb:
        return None
    try:
        r = subprocess.run([adb, "-s", target, "shell", cmd], capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def connect(target: str, *, env: Mapping[str, str] | None = None) -> None:
    """Ask adb for a connection. A device that is off simply does not answer, which is not an error."""
    adb = adb_path(env)
    if not adb:
        return
    try:
        subprocess.run([adb, "connect", target], capture_output=True, text=True, timeout=8, env=env)
    except (OSError, subprocess.SubprocessError):
        pass


def alive(dev: Device, *, env: Mapping[str, str] | None = None) -> bool:
    """Whether the device answers adb.

    A TCP connection is not enough here, because adb is often reached through a forwarded port, and
    the local end of a forward accepts connections whether or not the device behind it is on.
    """
    if shell(dev.adb, "echo ok", timeout=6, env=env) is not None:
        return True
    connect(dev.adb, env=env)
    return shell(dev.adb, "echo ok", timeout=6, env=env) is not None


def foreground(dev: Device, *, env: Mapping[str, str] | None = None) -> tuple[bool, str | None]:
    """(could the device be reached, the package in front).

    The package and not the activity, because an app can change its own activity without the
    app in front changing.
    """
    out = shell(dev.adb, "dumpsys activity activities | grep -m1 mResumedActivity", env=env)
    if out is None:
        return False, None
    m = re.search(r"u0 ([A-Za-z0-9_.]+)/", out)
    return True, (m.group(1) if m else None)


def launch(dev: Device, package: str, timeout: float = 10.0, *, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    # TV apps register their launcher under LEANBACK_LAUNCHER, not the phone LAUNCHER category.
    out = shell(dev.adb, f"monkey -p {package} -c android.intent.category.LEANBACK_LAUNCHER 1", timeout=timeout, env=env)
    if out is None:
        return False, "the device did not answer adb"
    if "No activities found" in out:
        return False, f"{package} has no TV launcher activity"
    return True, "launched"


def power_key(dev: Device, *, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    out = shell(dev.adb, "input keyevent KEYCODE_POWER", env=env)
    return (True, "power key sent") if out is not None else (False, "the device did not answer adb")
