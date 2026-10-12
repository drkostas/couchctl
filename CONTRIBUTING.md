# Contributing

Issues and pull requests are welcome, especially reports from television models that behave differently.

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Please add a test for every change in behaviour. The fake Samsung set is in `tests/fake_samsung.py` and the fake `adb` is in `tests/test_androidtv_and_config.py`. If you report a model, please include the output of `couchctl info <device>`.

## Writing for this repository

Everything here is public, including commit messages and pull request descriptions. Please write them for a user of the package. Do not name other projects, people or machines that use it, and keep real hostnames, IP addresses, paths and credentials out of code, tests and history (examples use `example.org` and `192.0.2.x`). A check on every pull request looks for terms that must not appear here.
