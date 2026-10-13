# Changelog

## Unreleased

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
