# Contributing

Issues and pull requests are welcome, especially reports from television models that behave differently.

```bash
python -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pytest
```

Please add a test for every change in behaviour. The fake Samsung set is in `tests/fake_samsung.py` and the fake `adb` is in `tests/test_androidtv_and_config.py`. If you report a model, please include the output of `couchctl info <device>`.
