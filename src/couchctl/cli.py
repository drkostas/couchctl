"""couchctl command line."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path
import urllib.request

from . import __version__, androidtv, power, redirect, samsung
from . import tokens as token_store
from .config import Config, default_path, load


def _say(*parts) -> None:
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *parts, flush=True)


def _targets(cfg: Config, names: list[str]):
    return [cfg.device(n) for n in names] if names else list(cfg.devices.values())


def cmd_state(cfg, args) -> int:
    for dev in _targets(cfg, args.devices):
        s = power.state(dev, on_their_network=not args.maybe_away)
        print(f"{dev.name}: {'on' if s else 'off' if s is False else 'unknown'}")
    return 0


def cmd_info(cfg, args) -> int:
    dev = cfg.device(args.device)
    if dev.kind != "samsung":
        print("info is for samsung devices", file=sys.stderr)
        return 2
    said = samsung.info(dev)
    print(json.dumps(said, indent=2) if said else f"{dev.name} is not answering")
    return 0 if said else 1


def cmd_pair(cfg, args) -> int:
    dev = cfg.device(args.device)
    if dev.kind != "samsung":
        print(f"{dev.name} is an androidtv device; allow the adb prompt on its screen instead", file=sys.stderr)
        return 2
    if args.again:
        token_store.forget(cfg.token_file, dev.name)
    print(f"Connecting to {dev.name}. Choose Allow on the television when it asks.", flush=True)
    outcome, why = samsung.pair(dev, cfg.token_file)
    print(f"{outcome}: {why}")
    return 0 if outcome == "success" else 1


def cmd_on(cfg, args) -> int:
    outcome, why = power.power_on(cfg.device(args.device))
    print(f"{outcome}: {why}")
    return 0 if outcome == "success" else 1


def cmd_off(cfg, args) -> int:
    outcome, why = power.power_off(cfg.device(args.device), cfg.token_file)
    print(f"{outcome}: {why}")
    return 0 if outcome == "success" else 1


def cmd_key(cfg, args) -> int:
    dev = cfg.device(args.device)
    if dev.kind != "samsung":
        out = androidtv.shell(dev.adb, f"input keyevent {args.key}")
        ok, why = (out is not None), ("sent" if out is not None else "the device did not answer adb")
    else:
        ok, why = samsung.send_key(dev, cfg.token_file, args.key)
    print(why)
    return 0 if ok else 1


def cmd_launch(cfg, args) -> int:
    ok, why = redirect.launch(cfg.device(args.device), args.app)
    print(why)
    return 0 if ok else 1


def cmd_front(cfg, args) -> int:
    dev = cfg.device(args.device)
    if dev.kind == "androidtv":
        reached, app = androidtv.foreground(dev)
    else:
        candidates = args.apps or [r.source for r in dev.redirects]
        reached, app = samsung.foreground(dev, candidates)
    print(app if app else ("none of the listed apps" if reached else "unreachable"))
    return 0 if reached else 1


def _reporter(url: str | None, token_env: str | None):
    def report(out: dict) -> None:
        _say(f"[{out['device']}] {out['outcome']}: {out.get('name') or out['from'] + ' -> ' + out['to']} ({out['message']})")
        if not url:
            return
        body = dict(out, at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        headers = {"content-type": "application/json", "user-agent": f"couchctl/{__version__}"}
        if token_env and os.environ.get(token_env):
            headers["authorization"] = f"Bearer {os.environ[token_env]}"
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:  # noqa: BLE001 - a lost report must not stop the watcher
            _say(f"[{out['device']}] report failed: {type(e).__name__}: {e}")
    return report


def cmd_watch(cfg, args) -> int:
    devices = [d for d in _targets(cfg, args.devices) if d.redirects]
    if not devices:
        print("no device in the config has a redirect", file=sys.stderr)
        return 1
    if any(d.kind == "androidtv" for d in devices) and not androidtv.adb_path():
        _say("adb is not installed, so androidtv devices cannot be watched")
        devices = [d for d in devices if d.kind != "androidtv"]
    for d in devices:
        _say(f"watching {d.name} every {args.interval}s: " + ", ".join(r.label() for r in d.redirects))
    perform = None
    if args.dry_run:
        def perform(dev, app_id):
            r = dev.redirect_for(app_id)
            return {"device": dev.name, "from": r.source, "to": r.target, "name": r.name,
                    "outcome": "dry-run", "message": "would launch the replacement"}
    redirect.watch_all(devices, poll_s=args.interval, perform=perform,
                       on_redirect=_reporter(None if args.dry_run else args.report_url, args.report_token_env))
    return 0


SKILL_SRC = Path(__file__).parent / "skill" / "SKILL.md"


def install_skill(target: str = "~/.claude/skills") -> Path:
    """Copy the Claude Code skill that comes with couchctl into a skills folder."""
    dest = Path(target).expanduser() / "couchctl"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL_SRC, dest / "SKILL.md")
    return dest / "SKILL.md"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="couchctl", description="Control Samsung and Android TVs on your network.")
    p.add_argument("--config", default=None, help="config file (default $COUCHCTL_CONFIG or ~/.config/couchctl/config.toml)")
    p.add_argument("--version", action="version", version=f"couchctl {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("state", help="whether each device is on")
    s.add_argument("devices", nargs="*")
    s.add_argument("--maybe-away", action="store_true", help="this machine may not be on the devices' network; report unknown")
    s.set_defaults(fn=cmd_state)

    s = sub.add_parser("info", help="what a samsung set says about itself")
    s.add_argument("device")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("pair", help="get a remote-control token from a samsung set")
    s.add_argument("device")
    s.add_argument("--again", action="store_true", help="forget the held token first")
    s.set_defaults(fn=cmd_pair)

    for name, fn, text in (("on", cmd_on, "wake a device by wake-on-LAN"), ("off", cmd_off, "switch a device off")):
        s = sub.add_parser(name, help=text)
        s.add_argument("device")
        s.set_defaults(fn=fn)

    s = sub.add_parser("key", help="press a remote key (KEY_HOME on samsung, KEYCODE_HOME on androidtv)")
    s.add_argument("device")
    s.add_argument("key")
    s.set_defaults(fn=cmd_key)

    s = sub.add_parser("launch", help="open an app by its id (samsung) or package (androidtv)")
    s.add_argument("device")
    s.add_argument("app")
    s.set_defaults(fn=cmd_launch)

    s = sub.add_parser("front", help="which app is on screen")
    s.add_argument("device")
    s.add_argument("apps", nargs="*", help="samsung: app ids to check (default: the redirect sources)")
    s.set_defaults(fn=cmd_front)

    s = sub.add_parser("watch", help="redirect the remotes' app buttons, until stopped")
    s.add_argument("devices", nargs="*")
    s.add_argument("--interval", type=float, default=redirect.POLL_S)
    s.add_argument("--dry-run", action="store_true", help="log presses, launch nothing, report nothing")
    s.add_argument("--report-url", help="POST each redirect as JSON to this URL")
    s.add_argument("--report-token-env", help="name of an environment variable holding a bearer token for --report-url")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("skill", help="install the Claude Code skill for couchctl (needs no config)")
    s.add_argument("--dir", default="~/.claude/skills")
    s.set_defaults(fn=None)

    args = p.parse_args(argv)
    if args.cmd == "skill":
        print(f"installed {install_skill(args.dir)}")
        return 0
    path = args.config or default_path()
    try:
        cfg = load(path)
    except FileNotFoundError:
        print(f"no config at {path} (see examples/config.example.toml)", file=sys.stderr)
        return 2
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        return args.fn(cfg, args)
    except KeyError as e:
        print(e.args[0], file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
