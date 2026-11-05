from couchctl.cli import main


def test_skill_installs_without_a_config(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCHCTL_CONFIG", str(tmp_path / "missing.toml"))
    assert main(["skill", "--dir", str(tmp_path)]) == 0
    text = (tmp_path / "couchctl" / "SKILL.md").read_text()
    assert text.startswith("---\nname: couchctl\n")


def test_skill_says_what_to_do_when_sdb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCHCTL_CONFIG", str(tmp_path / "missing.toml"))
    assert main(["skill", "--dir", str(tmp_path)]) == 0
    text = (tmp_path / "couchctl" / "SKILL.md").read_text()
    # the catalogue names the symptom, the relay command and the option that installs through it
    assert "failed to connect to remote target" in text
    assert "couchctl relay --from" in text
    assert "--sdb 127.0.0.1:26102" in text
