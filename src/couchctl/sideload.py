"""Sideloaded apps: install or update one on a Samsung set over sdb, or on an Android TV over adb,
and check the result against what the device itself reports.

A Samsung set takes a signed `.wgt`. Developer mode must be on, with the machine that runs `sdb` as
its Host PC IP, and the package must be signed with a distributor certificate that lists the set
(see the skill). `resign` signs a package from its author with your own Tizen Studio profile.

An Android TV or projector takes an `.apk` over adb, with network debugging on.

Success is never read from the installer's words. On a Samsung set the app must be listed by
`vd_applist` with the package's version afterwards. On Android the installed file must match the
`.apk` byte for byte.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import androidtv
from .config import Device

#: the port a Samsung set opens for sdb once developer mode is active
SDB_PORT = 26101
#: where Tizen Studio itself puts a package before it installs it
REMOTE_WGT = "/home/owner/share/tmp/sdk_tools/tmp/app.wgt"
#: A set that has just woken can answer "Work is queuing" for ten minutes or more while it updates
#: its own apps, and the install only starts after that.
INSTALL_WAIT = 900.0
_W3C = "{http://www.w3.org/ns/widgets}"
_TIZEN = "{http://tizen.org/ns/widgets}"
#: one field of a `vd_applist` record: `--------------app_version   =2.1.1-------------`
_APPLIST_FIELD = re.compile(r"^-*(app_id|app_version)\s*=(.*?)-{5,}\s*$", re.M)


class SideloadError(RuntimeError):
    """A step that could not be attempted at all, with the reason."""


@dataclass
class Installed:
    ok: bool
    version: str | None
    detail: str


def _tool(name: str, var: str, env: Mapping[str, str] | None) -> str | None:
    src = os.environ if env is None else env
    return src.get(var) or shutil.which(name, path=src.get("PATH"))


def sdb_path(env: Mapping[str, str] | None = None) -> str | None:
    """COUCHCTL_SDB, or sdb found on the PATH (Tizen Studio keeps it in `tools/`)."""
    return _tool("sdb", "COUCHCTL_SDB", env)


def tizen_path(env: Mapping[str, str] | None = None) -> str | None:
    """COUCHCTL_TIZEN, or tizen found on the PATH (Tizen Studio keeps it in `tools/ide/bin/`)."""
    return _tool("tizen", "COUCHCTL_TIZEN", env)


def _tail(text: str, n: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else "..." + text[-n:]


def wgt_info(wgt: str | os.PathLike) -> tuple[str, str]:
    """(app id, version) from a `.wgt`'s config.xml."""
    try:
        with zipfile.ZipFile(wgt) as z:
            root = ET.fromstring(z.read("config.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as e:
        raise SideloadError(f"{wgt} is not a readable .wgt package: {e}") from e
    app = root.find(f"{_TIZEN}application")
    if app is None or not app.get("id"):
        raise SideloadError(f"{wgt} has no tizen:application id in its config.xml")
    return app.get("id"), root.get("version") or ""


def _extract(wgt: str | os.PathLike, dest: Path) -> None:
    """Unpack a package, refusing any member that would land outside `dest`."""
    with zipfile.ZipFile(wgt) as z:
        for name in z.namelist():
            p = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts:
                raise SideloadError(f"{wgt} has a member outside its own folder: {name}")
        z.extractall(dest)


def resign(wgt: str | os.PathLike, profile: str, work_dir: str | os.PathLike, *,
           env: Mapping[str, str] | None = None) -> Path:
    """A copy of `wgt` signed with the Tizen Studio security profile `profile`, made in `work_dir`.

    A set in developer mode installs only packages signed for it, so a package from its author has
    to be signed again with a distributor certificate that lists the set. The author's signatures
    are removed before signing."""
    tizen = tizen_path(env)
    if not tizen:
        raise SideloadError("the tizen command was not found; set COUCHCTL_TIZEN to Tizen Studio's tools/ide/bin/tizen")
    src = Path(work_dir) / "package"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    _extract(wgt, src)
    for old in src.glob("*signature*.xml"):
        old.unlink()
    try:
        r = subprocess.run([tizen, "package", "-t", "wgt", "-s", profile, "--", str(src)],
                           capture_output=True, text=True, timeout=300, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise SideloadError(f"tizen package did not run: {e}") from e
    made = sorted(src.glob("*.wgt"))
    if r.returncode != 0 or not made:
        raise SideloadError(f"tizen package did not sign it with {profile!r}: {_tail(r.stdout + r.stderr)}")
    return made[0]


def parse_applist(text: str) -> dict[str, str]:
    """{app id: version} from the output of `vd_applist`."""
    apps: dict[str, str] = {}
    current = None
    for m in _APPLIST_FIELD.finditer(text or ""):
        key, value = m.group(1), m.group(2).strip()
        if key == "app_id":
            current = value
            apps[current] = ""
        elif current is not None:
            apps[current] = value
    return apps


def _sdb(sdb: str, env: Mapping[str, str] | None, *args: str, timeout: float = 30.0) -> tuple[int | None, str]:
    """(exit code, output) of one sdb command; None for a command that did not finish in time."""
    try:
        r = subprocess.run([sdb, *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, out
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def samsung_install(dev: Device, wgt: str | os.PathLike, *, port: int = SDB_PORT, wait: float = INSTALL_WAIT,
                    keep_server: bool = False, env: Mapping[str, str] | None = None) -> Installed:
    """Install (or update) a signed `.wgt` on a Samsung set and check that the set lists it with the
    package's version afterwards.

    The sdb server that `sdb connect` starts listens on every network interface, so it is stopped
    when the install is over. Pass `keep_server` when something else on this machine uses it."""
    sdb = sdb_path(env)
    if not sdb:
        raise SideloadError("sdb was not found; set COUCHCTL_SDB to Tizen Studio's tools/sdb")
    app_id, version = wgt_info(wgt)
    target = f"{dev.host}:{port}"
    try:
        _, said = _sdb(sdb, env, "connect", target)
        if f"connected to {target}" not in said or "failed" in said.lower():
            return Installed(False, None, f"sdb could not connect to {target} ({_tail(said)}). Developer mode must "
                                          "be on with this machine as Host PC IP, applied by unplugging the set")
        rc, said = _sdb(sdb, env, "-s", target, "push", str(wgt), REMOTE_WGT, timeout=300)
        if rc != 0:
            return Installed(False, None, f"could not copy the package to the set: {_tail(said)}")
        rc, said = _sdb(sdb, env, "-s", target, "shell", "0", "vd_appinstall", app_id, REMOTE_WGT, timeout=wait)
        _, listing = _sdb(sdb, env, "-s", target, "shell", "0", "vd_applist", timeout=60)
        have = parse_applist(listing).get(app_id)
        ok = have is not None and (have == version or not version)
        waited = f" (the install had not finished after {wait:.0f} s)" if rc is None else ""
        return Installed(ok, have or None,
                         f"{app_id} is {'at ' + have if have else 'not listed'} after installing {version or 'it'}"
                         f"{waited}; the set said: {_tail(said)}")
    finally:
        _sdb(sdb, env, "disconnect", target)
        if not keep_server:
            _sdb(sdb, env, "kill-server")


def samsung_version(dev: Device, app_id: str, timeout: float = 4.0) -> str | None:
    """The version a Samsung set reports for an installed app on port 8001, or None when the set
    did not answer or does not have it. Needs no developer mode and no pairing."""
    try:
        url = f"http://{dev.host}:{dev.api_port}/api/v2/applications/{app_id}"
        with urllib.request.urlopen(url, timeout=timeout) as r:
            d = json.loads(r.read() or b"{}") or {}
    except Exception:  # noqa: BLE001
        return None
    return d.get("version") or None


def android_version(dev: Device, package: str, *, env: Mapping[str, str] | None = None) -> str | None:
    """The versionName of an installed package, or None when the device did not answer or does not
    have it."""
    out = androidtv.shell(dev.adb, f"dumpsys package {package}", timeout=15, env=env)
    m = re.search(r"versionName=(\S+)", out or "")
    return m.group(1) if m else None


def android_install(dev: Device, apk: str | os.PathLike, package: str, *,
                    env: Mapping[str, str] | None = None) -> Installed:
    """Install (or update) an `.apk` over adb, keeping the app's data, and check that the installed
    file is the same file byte for byte. adb's own "Success" is not taken as proof."""
    adb = androidtv.adb_path(env)
    if not adb:
        raise SideloadError("adb was not found; set COUCHCTL_ADB or put it on the PATH")
    try:
        r = subprocess.run([adb, "-s", dev.adb, "install", "-r", str(apk)],
                           capture_output=True, text=True, timeout=600, env=env)
        said = (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        said = str(e)
    where = (androidtv.shell(dev.adb, f"pm path {package}", timeout=15, env=env) or "").strip().splitlines()
    path = where[0].strip().removeprefix("package:") if where else ""
    remote = (androidtv.shell(dev.adb, f"md5sum {path}", timeout=60, env=env) or "").split()[:1] if path else []
    local = hashlib.md5(Path(apk).read_bytes()).hexdigest()
    ok = remote == [local]
    version = android_version(dev, package, env=env)
    return Installed(ok, version, f"{package} is {'at ' + version if version else 'not installed'}; the installed "
                                  f"file {'matches' if ok else 'does not match'} the .apk; adb said: {_tail(said)}")


def latest_release(repo: str, timeout: float = 15.0) -> dict:
    """{"tag": ..., "assets": {name: download url}} for a GitHub repository's latest release."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return {"tag": d.get("tag_name") or "",
            "assets": {a["name"]: a["browser_download_url"] for a in d.get("assets") or []}}


def download(url: str, dest: str | os.PathLike, timeout: float = 120.0) -> Path:
    """Fetch `url` to `dest`, through a temporary name so a broken download never looks finished."""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=timeout) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f)
    part.replace(dest)
    return dest


def version_key(v: str) -> tuple:
    """A sort key for versions such as "v2.1.1" or "2.0.2": numbers compare as numbers."""
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"[.\-+]", (v or "").lstrip("vV")) if p)


def newer(candidate: str, installed: str | None) -> bool:
    """Whether `candidate` is a later version than `installed` (anything is newer than nothing)."""
    if not installed:
        return True
    try:
        return version_key(candidate) > version_key(installed)
    except TypeError:          # a number against a word: compare them as text
        return str(version_key(candidate)) > str(version_key(installed))
