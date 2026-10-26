# Changelog

## Unreleased

- Philips Hue lights: a device of kind `hue` is a Hue bridge. `couchctl pair` gets its application key (press the bridge's link button within 30 seconds), and `couchctl hue discover|lights|groups|scenes|light|group|scene` finds the bridge, lists its lights, rooms, zones and scenes, and switches, dims and colors a light or a whole room, or shows a scene. `couchctl.hue` has the same from Python: `discover`, `config`, `pair`, and a `Bridge` with `lights()`, `groups()`, `scenes()`, `set_light()`, `set_group()` and `recall_scene()`, over the bridge's local CLIP v2 API. Colors are given as `#rrggbb` and whites in kelvin. `couchctl state` and `couchctl watch` with no device named leave a bridge out, since it is not a screen
- `couchctl text DEVICE "some words" [--enter]`, and `samsung.send_text` / `androidtv.send_text`: type into the text field a screen has open, for the times a remote's on-screen keyboard is slow to use. Samsung sets take the text over the remote websocket (one `SendInputString`, the text base64-encoded, and a `SendInputEnd` when `--enter`/`submit` is given, which acts like Done); Android TV takes it over adb (`input text`, spaces sent as `%s` and shell-special characters escaped, then `KEYCODE_ENTER` to submit). The field must already be focused; neither screen reports what reached it, so confirm by looking at the screen
- `couchctl relay --from ADDRESS TARGET:PORT` and `couchctl.relay.serve`: a TCP relay on the loopback whose outgoing connections start from a chosen source address. A Samsung set in developer mode accepts sdb only from the one Host PC IP entered on it; when the machine still holds that address on some interface, `sdb connect 127.0.0.1:26102` then reaches the set as the host it trusts
- `couchctl sideload` and `couchctl.sideload` install or update a sideloaded app: a `.wgt` on a Samsung set over sdb (optionally signed again with your Tizen Studio profile), or an `.apk` on an Android TV over adb. Success is read from the device afterwards (the set's app list at the package's version, or the installed file matching the `.apk`), never from the installer's words. The sdb server, which listens on every network interface, is stopped when the install ends. `latest_release`, `download` and `newer` help keep an app published on GitHub current
- `power_on` (and `couchctl on`) checks that the device came on. It sends the wake packet every 2 seconds, to the broadcast address and to the device's own /24 network, until the device answers or 25 seconds pass, and reports `failure` with the likely reasons when it never answered. Before, it reported `success` as soon as one packet left, whether or not the set woke, and a set switched off moments earlier, still shutting down, ignored that one packet
- A Samsung set that no longer accepts the held token shows an "allow this device?" prompt and, after 30 seconds, says `ms.channel.timeOut`. couchctl waited exactly 30 seconds, so it reported its own `TimeoutError` instead. It now waits longer than the prompt, and `send_key`, `power_off` and `pair` say that nobody answered the prompt and how to pair again
- `send_key` sends the key only when the set opened the remote channel (`ms.channel.connect`). Before, a key sent after any other greeting was reported as sent

## 0.3.0

- The adb functions (and `power.state`, `power_off`, `redirect.peek`, `launch`, `watch`, `watch_all`) take an optional `env` for the adb process. With it, `adb` is also looked up on that env's PATH

## 0.2.0

- A Claude Code skill, installed with `couchctl skill`

## 0.1.0

First release.

- Power state, power on (wake-on-LAN) and power off for Samsung Tizen sets and Android TV devices
- Pairing with Samsung sets, with the token kept in a private file
- Launch apps, press remote keys, read the app in front
- `couchctl watch` redirects the remote's app buttons, with a dry-run mode and an optional JSON webhook
