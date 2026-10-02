from couchctl.cli import main


def test_skill_installs_without_a_config(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCHCTL_CONFIG", str(tmp_path / "missing.toml"))
    assert main(["skill", "--dir", str(tmp_path)]) == 0
    text = (tmp_path / "couchctl" / "SKILL.md").read_text()
    assert text.startswith("---\nname: couchctl\n")
