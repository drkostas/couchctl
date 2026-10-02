"""Send a remote's hardwired app buttons to a different app.

The app buttons on these remotes cannot be remapped. On Tizen they are not in the key table an app
can register, and the remote channel only sends, so a press never reaches the network. On the
Android TV projectors tested, the buttons never reach the input layer either. What can be seen is
the result, the app the button opened. So the watcher polls which app is in front, and when one
with a redirect becomes visible it launches the replacement on top.

That makes it a redirect and not a remap. The original app does open for a second or two before it
is replaced, and the redirect only works while the watcher runs on the same network as the screens.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from . import androidtv, samsung
from .config import Device

POLL_S = 1.0


def peek(dev: Device) -> tuple[bool, str | None]:
    """(could the device be reached, which app with a redirect is in front)."""
    sources = [r.source for r in dev.redirects]
    if dev.kind == "androidtv":
        reached, package = androidtv.foreground(dev)
        if not reached:
            # adb drops a device that was switched off and does not reconnect by itself.
            androidtv.connect(dev.adb)
        return reached, (package if package in sources else None)
    return samsung.foreground(dev, sources)


def launch(dev: Device, app_id: str) -> tuple[bool, str]:
    if dev.kind == "androidtv":
        return androidtv.launch(dev, app_id)
    return samsung.launch(dev, app_id)


def default_perform(dev: Device, app_id: str) -> dict:
    r = dev.redirect_for(app_id)
    ok, why = launch(dev, r.target)
    return {"device": dev.name, "from": r.source, "to": r.target, "name": r.name,
            "outcome": "success" if ok else "failure", "message": why}


def watch(dev: Device, *, polls: int | None = None, seconds: float = 0.0, poll_s: float = POLL_S,
          perform: Callable[[Device, str], dict] | None = None,
          on_redirect: Callable[[dict], None] | None = None,
          peek_fn: Callable[[Device], tuple[bool, str | None]] = peek) -> list[dict]:
    """Poll the screen and answer every new press of a redirected button.

    It acts only on a rising edge, when an app becomes visible. Somebody who goes back to the
    original app on purpose is not fought by the loop.

    The edge is seeded from what is on screen at start, and again whenever the device comes back
    after being unreachable. Whatever is in front at that moment was chosen before the watcher could
    see it, so it is never treated as a press. Without this, a restart or a laptop walking back into
    range would redirect somebody who opened that app on purpose minutes earlier.
    """
    perform = perform or default_perform
    done: list[dict] = []
    seen, was = peek_fn(dev)
    lost = not seen
    end = time.time() + seconds if seconds else None
    n = 0
    while (polls is None or n < polls) and (end is None or time.time() < end):
        reached, now = peek_fn(dev)
        n += 1
        if not reached:
            lost = True  # say nothing about the screen, and keep `was`
        elif lost:
            was, lost = now, False  # back in reach: re-seed, never fire
        else:
            if now and now != was and dev.redirect_for(now):
                out = perform(dev, now)
                done.append(out)
                if on_redirect:
                    on_redirect(out)
            was = now
        if poll_s:
            time.sleep(poll_s)
    return done


def watch_all(devices: list[Device], **kwargs) -> None:
    """One thread per device, so a set that is off cannot slow the poll of one that is on."""
    def run(dev: Device) -> None:
        try:
            if dev.kind == "androidtv":
                androidtv.connect(dev.adb)
            watch(dev, **kwargs)
        except Exception as e:  # noqa: BLE001 - one device failing must not stop the others
            print(f"[{dev.name}] stopped: {type(e).__name__}: {e}", flush=True)

    threads = [threading.Thread(target=run, args=(d,), daemon=True) for d in devices if d.redirects]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
