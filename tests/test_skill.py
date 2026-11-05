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
    # the catalogue names the symptom, says that sideload retries through a relay by itself, and
    # names the relay command for a person's own sdb commands
    assert "failed to connect to remote target" in text
    assert "starts a relay by itself" in text
    assert "couchctl relay <address>:26101" in text
    assert "-s 127.0.0.1:26102" in text
