import hashlib
import stat
import zipfile

import pytest

from couchctl import sideload
from couchctl.cli import main
from couchctl.config import Device

CONFIG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<widget xmlns="http://www.w3.org/ns/widgets" xmlns:tizen="http://tizen.org/ns/widgets"
        id="http://example.org/app" version="{version}">
    <tizen:application id="Abc123defG.Player" package="Abc123defG" required_version="3.0"/>
    <name>Player</name>
</widget>
"""

# A fake sdb. It logs every call, and its app list reports $FAKE_SDB_HAVE as the installed version.
FAKE_SDB = """#!/bin/sh
echo "$@" >> "$FAKE_SDB_LOG"
case "$*" in
  "connect "*) [ -n "$FAKE_SDB_REFUSE" ] && { echo "failed to connect to remote target '$2'"; exit 1; }
               echo "connecting to $2 ..."; echo "connected to $2" ;;
  *vd_appinstall*) echo "app_id[Abc123defG.Player] install start"; echo "app_id[Abc123defG.Player] install completed" ;;
  *vd_applist*) [ -z "$FAKE_SDB_HAVE" ] && exit 0
     echo "---------------------------------------------------------------------------------------------"
     echo "--------------app_id                                =Abc123defG.Player-------------"
     echo "--------------app_title                             =Player-------------"
     echo "--------------app_version                           =$FAKE_SDB_HAVE-------------"
     echo "--------------app_id                                =Other0000.App-------------"
     echo "--------------app_version                           =9.9-------------" ;;
esac
exit 0
"""

# A fake tizen CLI: `tizen package -t wgt -s <profile> -- <dir>` zips the folder into <dir>/Player.wgt.
FAKE_TIZEN = """#!/bin/sh
echo "$@" >> "$FAKE_TIZEN_LOG"
[ "$5" = "nosuchprofile" ] && { echo "Profile not found"; exit 1; }
for last; do :; done
cd "$last" && ls > "$FAKE_TIZEN_LOG.files" && /usr/bin/zip -q -r Player.wgt . && echo "Package File Location: $last/Player.wgt"
"""


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def make_wgt(path, version="2.1.1", extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("config.xml", CONFIG_XML.format(version=version))
        z.writestr("index.html", "<html></html>")
        z.writestr("author-signature.xml", "<Signature/>")
        z.writestr("signature1.xml", "<Signature/>")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return path


@pytest.fixture
def sdb(tmp_path, monkeypatch):
    log = tmp_path / "sdb.log"
    monkeypatch.setenv("COUCHCTL_SDB", str(_script(tmp_path / "sdb", FAKE_SDB)))
    monkeypatch.setenv("FAKE_SDB_LOG", str(log))
    monkeypatch.setenv("FAKE_SDB_HAVE", "2.1.1")
    return log


tv = Device(name="tv", kind="samsung", host="192.0.2.10")


def test_wgt_info_reads_the_app_id_and_version(tmp_path):
    assert sideload.wgt_info(make_wgt(tmp_path / "a.wgt")) == ("Abc123defG.Player", "2.1.1")


def test_parse_applist_pairs_each_id_with_its_version():
    text = ("--------------app_id      =One.App-------------\n--------------app_version =1.0-beta-------------\n"
            "--------------app_id      =Two.App-------------\n--------------app_version =2.1.1-------------\n")
    assert sideload.parse_applist(text) == {"One.App": "1.0-beta", "Two.App": "2.1.1"}


def test_samsung_install_is_checked_against_the_set_and_stops_the_server(sdb, tmp_path):
    done = sideload.samsung_install(tv, make_wgt(tmp_path / "a.wgt"))
    assert done.ok and done.version == "2.1.1"
    calls = sdb.read_text().splitlines()
    assert calls[0] == "connect 192.0.2.10:26101"
    assert f"push {tmp_path / 'a.wgt'} {sideload.REMOTE_WGT}" in calls[1]
    assert "vd_appinstall Abc123defG.Player" in calls[2]
    # the sdb server listens on every interface, so it does not outlive the install
    assert calls[-2:] == ["disconnect 192.0.2.10:26101", "kill-server"]


def test_an_install_the_set_does_not_list_at_the_new_version_is_not_success(sdb, tmp_path, monkeypatch):
    # "install completed" from the set is not taken as proof
    monkeypatch.setenv("FAKE_SDB_HAVE", "2.0.1")
    done = sideload.samsung_install(tv, make_wgt(tmp_path / "a.wgt", version="2.1.1"))
    assert not done.ok and done.version == "2.0.1" and "at 2.0.1 after installing 2.1.1" in done.detail


def test_a_refused_connection_says_what_to_check_and_installs_nothing(sdb, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SDB_REFUSE", "1")
    done = sideload.samsung_install(tv, make_wgt(tmp_path / "a.wgt"))
    assert not done.ok and "Host PC IP" in done.detail
    assert not any("vd_appinstall" in c for c in sdb.read_text().splitlines())
    assert sdb.read_text().splitlines()[-1] == "kill-server"


def test_keep_server_leaves_the_server_running(sdb, tmp_path):
    sideload.samsung_install(tv, make_wgt(tmp_path / "a.wgt"), keep_server=True)
    assert "kill-server" not in sdb.read_text()


def test_resign_drops_the_author_signatures_and_signs_with_the_profile(tmp_path, monkeypatch):
    log = tmp_path / "tizen.log"
    monkeypatch.setenv("COUCHCTL_TIZEN", str(_script(tmp_path / "tizen", FAKE_TIZEN)))
    monkeypatch.setenv("FAKE_TIZEN_LOG", str(log))
    out = sideload.resign(make_wgt(tmp_path / "a.wgt"), "myprofile", tmp_path / "work")
    assert out.name == "Player.wgt" and out.exists()
    assert log.read_text().split()[:5] == ["package", "-t", "wgt", "-s", "myprofile"]
    files = (tmp_path / "tizen.log.files").read_text().split()
    assert "config.xml" in files and not [f for f in files if "signature" in f]


def test_resign_reports_a_profile_the_cli_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCHCTL_TIZEN", str(_script(tmp_path / "tizen", FAKE_TIZEN)))
    monkeypatch.setenv("FAKE_TIZEN_LOG", str(tmp_path / "tizen.log"))
    with pytest.raises(sideload.SideloadError, match="nosuchprofile"):
        sideload.resign(make_wgt(tmp_path / "a.wgt"), "nosuchprofile", tmp_path / "work")


def test_a_package_with_a_member_outside_its_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCHCTL_TIZEN", str(_script(tmp_path / "tizen", FAKE_TIZEN)))
    bad = make_wgt(tmp_path / "bad.wgt", extra={"../escape.txt": "x"})
    with pytest.raises(sideload.SideloadError, match="outside"):
        sideload.resign(bad, "myprofile", tmp_path / "work")
    assert not (tmp_path / "escape.txt").exists()


FAKE_ADB = """#!/bin/sh
echo "$@" >> "$FAKE_ADB_LOG"
case "$*" in
  *" install -r "*) echo "Success" ;;
  *"pm path"*) echo "package:/data/app/com.example.player/base.apk" ;;
  *md5sum*) echo "$FAKE_ADB_MD5  /data/app/com.example.player/base.apk" ;;
  *"dumpsys package"*) echo "    versionName=2.0.2" ;;
esac
exit 0
"""


@pytest.fixture
def adb(tmp_path, monkeypatch):
    monkeypatch.setenv("COUCHCTL_ADB", str(_script(tmp_path / "adb", FAKE_ADB)))
    monkeypatch.setenv("FAKE_ADB_LOG", str(tmp_path / "adb.log"))
    return tmp_path / "adb.log"


box = Device(name="box", kind="androidtv", adb="192.0.2.20:5555")


def test_android_install_is_checked_byte_for_byte(adb, tmp_path, monkeypatch):
    apk = tmp_path / "player.apk"
    apk.write_bytes(b"an apk")
    monkeypatch.setenv("FAKE_ADB_MD5", hashlib.md5(b"an apk").hexdigest())
    done = sideload.android_install(box, apk, "com.example.player")
    assert done.ok and done.version == "2.0.2"
    assert f"-s 192.0.2.20:5555 install -r {apk}" in adb.read_text()


def test_android_success_with_a_different_file_installed_is_not_success(adb, tmp_path, monkeypatch):
    apk = tmp_path / "player.apk"
    apk.write_bytes(b"an apk")
    monkeypatch.setenv("FAKE_ADB_MD5", "0" * 32)
    done = sideload.android_install(box, apk, "com.example.player")
    assert not done.ok and "does not match" in done.detail


def test_newer_compares_versions_as_numbers():
    assert sideload.newer("v2.1.10", "2.1.9")
    assert not sideload.newer("2.1.1", "2.1.1")
    assert not sideload.newer("v2.0.2", "2.1.1")
    assert sideload.newer("1.0", None)


def test_the_command_installs_on_a_samsung_set(sdb, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[devices.tv]\nkind = "samsung"\nhost = "192.0.2.10"\n')
    assert main(["--config", str(cfg), "sideload", "tv", str(make_wgt(tmp_path / "a.wgt"))]) == 0


def test_the_command_needs_a_package_for_an_android_device(adb, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[devices.box]\nkind = "androidtv"\nadb = "192.0.2.20:5555"\n')
    apk = tmp_path / "p.apk"
    apk.write_bytes(b"x")
    assert main(["--config", str(cfg), "sideload", "box", str(apk)]) == 2
