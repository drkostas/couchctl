"""couchctl command line."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
import urllib.request

from . import __version__, androidtv, hue, keyboard, power, redirect, samsung, sideload
from . import tokens as token_store
from .config import SCREENS, Config, default_path, load


def _say(*parts) -> None:
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *parts, flush=True)


def _targets(cfg: Config, names: list[str]):
    """The named devices, or every screen in the config (a Hue bridge is not a screen)."""
    return [cfg.device(n) for n in names] if names else [d for d in cfg.devices.values() if d.kind in SCREENS]


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
    if dev.kind == "hue":
        if args.again:
            token_store.forget(cfg.token_file, dev.name)
        elif token_store.read(cfg.token_file, dev.name):
            print(f"success: {dev.name} is already paired (use --again to pair it again)")
            return 0
        print(f"Press the round link button on {dev.name} now. Waiting {hue.PAIR_WAIT:.0f} seconds.", flush=True)
        out = hue.pair(dev.host, "couchctl#" + dev.name)
        if out["outcome"] != "success":
            print(f"{out['outcome']}: {out['why']}")
            return 1
        token_store.write(cfg.token_file, dev.name, out["key"])
        print(f"success: {dev.name} is paired, and its key is kept in {cfg.token_file}")
        return 0
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


def cmd_text(cfg, args) -> int:
    dev = cfg.device(args.device)
    if dev.kind == "androidtv":
        ok, why = androidtv.send_text(dev, args.text, submit=args.enter)
    else:
        ok, why = samsung.send_text(dev, cfg.token_file, args.text, submit=args.enter)
    print(why)
    return 0 if ok else 1


def _bridge(cfg, name: str) -> hue.Bridge:
    dev = cfg.device(name)
    if dev.kind != "hue":
        raise KeyError(f"{dev.name} is not a hue bridge")
    key = token_store.read(cfg.token_file, dev.name)
    if not key:
        raise KeyError(f"{dev.name} is not paired yet: run couchctl pair {dev.name}")
    return hue.Bridge(dev.host, key)


def _hue_change(args) -> dict:
    return {"on": args.on, "brightness": args.brightness, "color": args.color, "kelvin": args.kelvin,
            "transition_ms": args.transition}


def cmd_hue(cfg, args) -> int:
    if args.action == "discover":
        found = hue.discover()
        for b in found:
            said = hue.config(b["host"])
            print(f"{b['host']}  {said.get('name', '?')}  {said.get('modelid', '?')}  id {b['id']}")
        if not found:
            print("no bridge found (the discovery service can list a new bridge a few minutes late)")
        return 0 if found else 1
    if not args.device:
        print(f"hue {args.action} needs the bridge's device name", file=sys.stderr)
        return 2
    bridge = _bridge(cfg, args.device)
    try:
        if args.action == "lights":
            for x in bridge.lights():
                level = "" if x["brightness"] is None else f" {x['brightness']:.0f}%"
                reach = " (not reachable)" if x["reachable"] is False else ""
                print(f"{x['name']}: {'on' if x['on'] else 'off'}{level}{reach}")
        elif args.action == "groups":
            for g in bridge.groups():
                print(f"{g['kind']} {g['name']}: {len(g['lights'])} lights")
        elif args.action == "scenes":
            names = {g["id"]: g["name"] for g in bridge.groups()}
            for x in bridge.scenes():
                print(f"{x['name']} ({names.get(x['group'], x['group_kind'] or 'everywhere')})")
        elif args.action == "light":
            light = bridge.set_light(args.target, **_hue_change(args))
            print(f"changed {light['name']}")
        elif args.action == "group":
            group = bridge.set_group(args.target, **_hue_change(args))
            print(f"changed {group['name']}")
        elif args.action == "scene":
            scene = bridge.recall_scene(args.target, group=args.group)
            print(f"showing {scene['name']}")
    except (hue.HueError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


def cmd_keyboard(cfg, args) -> int:
    km = keyboard.KEYMAPS.get(args.app)
    if km is None:
        print(f"no keyboard known for {args.app!r}; known: {', '.join(sorted(keyboard.KEYMAPS))}")
        return 1
    dev = cfg.device(args.device)
    if dev.kind == "androidtv":
        ok, why = androidtv.type_on_keyboard(dev, args.text, km, submit=args.enter)
    else:
        ok, why = samsung.type_on_keyboard(dev, cfg.token_file, args.text, km, submit=args.enter)
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


def cmd_sideload(cfg, args) -> int:
    dev = cfg.device(args.device)
    try:
        if dev.kind == "androidtv":
            if not args.package:
                print("an androidtv install needs --package (the app's package name)", file=sys.stderr)
                return 2
            done = sideload.android_install(dev, args.file, args.package)
        else:
            with tempfile.TemporaryDirectory() as work:
                wgt = sideload.resign(args.file, args.sign, work) if args.sign else args.file
                done = sideload.samsung_install(dev, wgt, keep_server=args.keep_server)
    except sideload.SideloadError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(("installed: " if done.ok else "not installed: ") + done.detail)
    return 0 if done.ok else 1


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

    s = sub.add_parser("pair", help="get a remote-control token from a samsung set, or a key from a hue bridge")
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

    s = sub.add_parser("text", help="type text into the field the screen has open (a search box, a login field)")
    s.add_argument("device")
    s.add_argument("text")
    s.add_argument("--enter", action="store_true", help="press Enter after, to submit")
    s.set_defaults(fn=cmd_text)

    s = sub.add_parser("keyboard", help="type into an app's own on-screen search keyboard by "
                       "walking it with the remote, for apps that ignore typed text (YouTube and "
                       "the like)")
    s.add_argument("device")
    s.add_argument("app", help="which app's keyboard: " + ", ".join(sorted(keyboard.KEYMAPS)))
    s.add_argument("text")
    s.add_argument("--enter", action="store_true", help="press the search button after")
    s.set_defaults(fn=cmd_keyboard)

    s = sub.add_parser("launch", help="open an app by its id (samsung) or package (androidtv)")
    s.add_argument("device")
    s.add_argument("app")
    s.set_defaults(fn=cmd_launch)

    s = sub.add_parser("front", help="which app is on screen")
    s.add_argument("device")
    s.add_argument("apps", nargs="*", help="samsung: app ids to check (default: the redirect sources)")
    s.set_defaults(fn=cmd_front)

    s = sub.add_parser("sideload", help="install or update an app from a .wgt (samsung) or an .apk (androidtv), and check it")
    s.add_argument("device")
    s.add_argument("file")
    s.add_argument("--sign", metavar="PROFILE", help="samsung: sign the package again with this Tizen Studio profile first")
    s.add_argument("--package", help="androidtv: the app's package name, to check the installed file")
    s.add_argument("--keep-server", action="store_true", help="samsung: leave the sdb server running afterwards")
    s.set_defaults(fn=cmd_sideload)

    s = sub.add_parser("watch", help="redirect the remotes' app buttons, until stopped")
    s.add_argument("devices", nargs="*")
    s.add_argument("--interval", type=float, default=redirect.POLL_S)
    s.add_argument("--dry-run", action="store_true", help="log presses, launch nothing, report nothing")
    s.add_argument("--report-url", help="POST each redirect as JSON to this URL")
    s.add_argument("--report-token-env", help="name of an environment variable holding a bearer token for --report-url")
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("hue", help="Philips Hue lights through the bridge: discover, lights, groups, scenes, light, group, scene")
    s.add_argument("action", choices=("discover", "lights", "groups", "scenes", "light", "group", "scene"))
    s.add_argument("device", nargs="?", help="the bridge's name in the config (not needed for discover)")
    s.add_argument("target", nargs="?", help="light, room, zone or scene, by name or id")
    onoff = s.add_mutually_exclusive_group()
    onoff.add_argument("--on", dest="on", action="store_const", const=True, default=None)
    onoff.add_argument("--off", dest="on", action="store_const", const=False)
    s.add_argument("--brightness", type=float, help="0 to 100; 0 switches it off")
    s.add_argument("--color", help="#rrggbb")
    s.add_argument("--kelvin", type=int, help="a white's color temperature, about 2000 to 6500")
    s.add_argument("--transition", type=int, help="milliseconds to change over")
    s.add_argument("--group", help="scene: the room or zone whose scene it is")
    s.set_defaults(fn=cmd_hue)

    s = sub.add_parser("relay", help="relay sdb from 127.0.0.1 to a set, connecting from the address it trusts (needs no config)")
    s.add_argument("target", help="the set's sdb address, HOST:PORT (port 26101)")
    s.add_argument("--from", dest="source", required=True, help="the address the set trusts as its Host PC IP")
    s.add_argument("--listen", type=int, default=26102, help="the loopback port to listen on")
    s.set_defaults(fn=None)

    s = sub.add_parser("skill", help="install the Claude Code skill for couchctl (needs no config)")
    s.add_argument("--dir", default="~/.claude/skills")
    s.set_defaults(fn=None)

    args = p.parse_args(argv)
    if args.cmd == "relay":
        from . import relay   # noqa: PLC0415
        host, _, port = args.target.rpartition(":")
        if not host or not port.isdigit():
            print("relay: the target is HOST:PORT", file=sys.stderr)
            return 2
        try:
            relay.serve(args.source, args.listen, host, int(port), say=lambda s: print(s, flush=True))
        except OSError as e:
            print(f"relay: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        return 0
    if args.cmd == "skill":
        print(f"installed {install_skill(args.dir)}")
        return 0
    if args.cmd == "hue" and args.action == "discover":
        return cmd_hue(None, args)
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
