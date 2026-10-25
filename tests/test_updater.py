"""In-app updates (backend/updater.py). No network: urlopen / Popen are replaced."""
import hashlib
import io
import json
import time

import pytest

import backend.updater as up

BIG = b"MZ" + b"\0" * 1_200_000            # > 1 MB so it passes the "too small" guard
SHA = hashlib.sha256(BIG).hexdigest()


class FakeResp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def release(version="0.4.0", sha_in_digest=True):
    name = f"DeepFind-Setup-{version}.exe"
    asset = {"name": name, "size": len(BIG), "browser_download_url": f"https://example.test/{name}"}
    if sha_in_digest:
        asset["digest"] = f"sha256:{SHA}"
    return {"tag_name": f"v{version}", "body": "- faster search", "html_url": "https://example.test/rel",
            "published_at": "2026-10-10T00:00:00Z", "assets": [asset]}


@pytest.fixture()
def updater(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFIND_UPDATES_DIR", str(tmp_path / "updates"))
    monkeypatch.setenv("DEEPFIND_UPDATE_REPO", "someone/deepfind")
    monkeypatch.setattr(up, "read_version", lambda: "0.3.0")
    monkeypatch.setattr(up, "self_update_blocker", lambda: "")
    return up.Updater()


def wait_state(u, want, timeout=5):
    end = time.time() + timeout
    while time.time() < end and u.state != want:
        time.sleep(0.02)
    return u.state == want


def test_version_compare():
    assert up.is_newer("0.3.1", "0.3.0")
    assert up.is_newer("v1.0", "0.9.9")
    assert up.is_newer("0.10.0", "0.9.0")             # numeric, not string, order
    assert not up.is_newer("0.3.0", "0.3.0")
    assert not up.is_newer("0.3", "0.3.0")
    assert not up.is_newer("garbage", "0.3.0")


def test_source_ignores_placeholder(monkeypatch):
    monkeypatch.setenv("DEEPFIND_UPDATE_REPO", "OWNER/REPO")
    assert up.source()["repo"] == ""
    monkeypatch.setenv("DEEPFIND_UPDATE_REPO", "not a repo")
    assert up.source()["repo"] == ""
    monkeypatch.setenv("DEEPFIND_UPDATE_REPO", "haripriya/DeepFind")
    assert up.source()["repo"] == "haripriya/DeepFind"


def test_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFIND_UPDATES_DIR", str(tmp_path))
    monkeypatch.setenv("DEEPFIND_UPDATE_REPO", "OWNER/REPO")
    u = up.Updater()
    info = u.check()
    assert info["configured"] is False and info["available"] is False and "no update source" in info["error"]


def test_check_finds_newer_release(updater, monkeypatch):
    monkeypatch.setattr(up, "_get_json", lambda url: release("0.4.0"))
    info = updater.check()
    assert info["state"] == "available" and info["available"] and info["latest"]["version"] == "0.4.0"
    assert "faster search" in info["latest"]["notes"]
    assert "url" not in info["latest"] and "sha256" not in info["latest"]      # not exposed to the page


def test_check_up_to_date(updater, monkeypatch):
    monkeypatch.setattr(up, "_get_json", lambda url: release("0.3.0"))
    info = updater.check()
    assert info["state"] == "up_to_date" and not info["available"]


def test_release_without_installer_is_an_error(updater, monkeypatch):
    rel = release()
    rel["assets"] = []
    monkeypatch.setattr(up, "_get_json", lambda url: rel)
    info = updater.check()
    assert info["state"] == "error" and "no DeepFind-Setup" in info["error"]


def test_sha_from_side_file(updater, monkeypatch):
    rel = release(sha_in_digest=False)
    rel["assets"].append({"name": "DeepFind-Setup-0.4.0.exe.sha256", "browser_download_url": "https://example.test/x.sha256"})
    monkeypatch.setattr(up, "_get_json", lambda url: rel)
    monkeypatch.setattr(up, "_get_text", lambda url: f"{SHA}  DeepFind-Setup-0.4.0.exe\n")
    assert up.fetch_latest()["sha256"] == SHA


def test_network_failure_keeps_previous_state(updater, monkeypatch):
    monkeypatch.setattr(up, "_get_json", lambda url: release("0.4.0"))
    updater.check()
    def boom(url):
        raise OSError("offline")
    monkeypatch.setattr(up, "_get_json", boom)
    info = updater.check()
    assert info["state"] == "available" and "internet" in info["error"]


def test_rate_limit_falls_back_to_web_redirect(updater, monkeypatch):
    import urllib.error
    def limited(url):
        raise urllib.error.HTTPError(url, 403, "rate limit", {}, None)
    monkeypatch.setattr(up, "_get_json", limited)
    monkeypatch.setattr(up, "_latest_tag_without_api", lambda repo: "v0.5.0")
    monkeypatch.setattr(up, "_get_text", lambda url: f"{SHA}  x")
    info = updater.check()
    assert info["state"] == "available" and info["latest"]["version"] == "0.5.0"
    assert updater.latest["url"] == "https://github.com/someone/deepfind/releases/download/v0.5.0/DeepFind-Setup-0.5.0-online.exe"  # small installer first


def test_download_verify_install(updater, monkeypatch, tmp_path):
    monkeypatch.setattr(up, "_get_json", lambda url: release("0.4.0"))
    monkeypatch.setattr(up, "_urlopen", lambda url, timeout=15, accept=None: FakeResp(BIG))
    launched = []
    monkeypatch.setattr(up.subprocess, "Popen", lambda args, **kw: launched.append(args))
    closed = []
    updater.set_quit_hook(lambda: closed.append(1))
    monkeypatch.setattr(up.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda self: fn()})())

    updater.check()
    updater.download()
    assert wait_state(updater, "ready"), updater.error
    assert (tmp_path / "updates" / "DeepFind-Setup-0.4.0.exe").stat().st_size == len(BIG)
    assert not list((tmp_path / "updates").glob("*.part"))
    updater.install()
    assert launched and launched[0][-1] == "/UPDATE" and launched[0][0].endswith("DeepFind-Setup-0.4.0.exe")
    assert closed == [1] and updater.state == "installing"


def test_corrupt_download_is_rejected(updater, monkeypatch, tmp_path):
    monkeypatch.setattr(up, "_get_json", lambda url: release("0.4.0"))
    bad = bytearray(BIG); bad[-1] ^= 1
    monkeypatch.setattr(up, "_urlopen", lambda url, timeout=15, accept=None: FakeResp(bytes(bad)))
    updater.check()
    updater.download()
    assert wait_state(updater, "available")
    assert "checksum" in updater.error
    assert not list((tmp_path / "updates").glob("DeepFind-Setup-*"))             # nothing left behind


def test_install_refused_for_portable_copy(updater, monkeypatch, tmp_path):
    monkeypatch.setattr(up, "_get_json", lambda url: release("0.4.0"))
    monkeypatch.setattr(up, "_urlopen", lambda url, timeout=15, accept=None: FakeResp(BIG))
    updater.check(); updater.download()
    assert wait_state(updater, "ready")
    monkeypatch.setattr(up, "self_update_blocker", lambda: "This copy is portable")
    with pytest.raises(up.UpdateError, match="portable"):
        updater.install()
    assert updater.state == "ready"


def test_download_requires_https(updater, monkeypatch):
    rel = release("0.4.0")
    rel["assets"][0]["browser_download_url"] = "http://example.test/DeepFind-Setup-0.4.0.exe"
    monkeypatch.setattr(up, "_get_json", lambda url: rel)
    updater.check()
    with pytest.raises(up.UpdateError, match="HTTPS"):
        updater.download()


def test_download_without_check_is_refused(updater):
    with pytest.raises(up.UpdateError):
        updater.download()
    with pytest.raises(up.UpdateError):
        updater.install()


def test_just_updated_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFIND_UPDATES_DIR", str(tmp_path))
    monkeypatch.setattr(up, "read_version", lambda: "0.3.0")
    (tmp_path / "state.json").write_text(json.dumps({"last_run_version": "0.2.0"}))
    assert up.Updater().info()["just_updated"] is True
    assert up.Updater().info()["just_updated"] is False          # only once: the version is remembered


def test_api_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend import main as m
    monkeypatch.setattr(m.updater, "check", lambda: {"state": "up_to_date", "available": False})
    c = TestClient(m.app)
    assert c.get("/api/update").json()["current"]
    assert c.post("/api/update/check").json()["state"] == "up_to_date"
    assert c.post("/api/update/install").status_code == 409          # nothing downloaded
    assert c.post("/api/update/download").status_code == 409


def test_prefers_small_online_installer():
    rel = release()
    full = rel["assets"][0]
    rel["assets"] = [full, dict(full, name="DeepFind-Setup-0.4.0-online.exe",
                                browser_download_url="https://example.test/DeepFind-Setup-0.4.0-online.exe")]
    got = up._from_github(rel)
    assert got["name"] == "DeepFind-Setup-0.4.0-online.exe" and got["version"] == "0.4.0"
    assert up.ASSET_RE.match("DeepFind-Setup-0.4.0-online.exe")


def test_private_repo_uses_api_asset_url_with_token(updater, monkeypatch, tmp_path):
    up.set_token("github_pat_" + "a" * 30)
    try:
        rel = release()
        rel["assets"][0]["url"] = "https://api.github.com/repos/someone/deepfind/releases/assets/7"
        got = up._from_github(rel)
        assert got["api_download"] and got["url"].endswith("/assets/7")
        assert updater.info()["token_set"]
        with pytest.raises(up.UpdateError):
            up.set_token("not a token!")
    finally:
        up.set_token("")
    assert not up.get_token()


def test_token_not_forwarded_to_storage_redirect():
    import urllib.request
    h = up._AuthRedirect()
    req = urllib.request.Request("https://api.github.com/repos/a/b/releases/assets/1",
                                 headers={"Authorization": "Bearer x"})
    new = h.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/f")
    assert "Authorization" not in new.headers and new.get_header("Authorization") is None


# ---- choosing the repository inside the app ----------------------------------------------

def test_normalize_repo_accepts_links_and_names():
    n = up.normalize_repo
    assert n("owner/name") == "owner/name"
    assert n("  https://github.com/owner/name  ") == "owner/name"
    assert n("https://github.com/owner/name.git") == "owner/name"
    assert n("github.com/owner/name/releases/latest?x=1") == "owner/name"
    assert n("") == ""
    for bad in ("justaword", "https://example.com/a/b/c d", "OWNER/REPO", "a b/c"):
        with pytest.raises(up.UpdateError):
            n(bad)


def test_user_repo_overrides_built_in_but_not_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFIND_UPDATES_DIR", str(tmp_path / "updates"))
    monkeypatch.delenv("DEEPFIND_UPDATE_REPO", raising=False)
    monkeypatch.setattr(up, "ROOT", tmp_path)
    (tmp_path / "update_source.json").write_text('{"repo": "built/in"}')
    assert up.source()["repo"] == "built/in"
    assert up.set_repo("https://github.com/mine/app") == "mine/app"
    assert up.source()["repo"] == "mine/app" and up.source()["default_repo"] == "built/in"
    monkeypatch.setenv("DEEPFIND_UPDATE_REPO", "env/wins")
    assert up.source()["repo"] == "env/wins"
    monkeypatch.delenv("DEEPFIND_UPDATE_REPO")
    up.set_repo("")                                            # reset -> built-in again
    assert up.source()["repo"] == "built/in"


def test_changing_repo_forgets_old_result_and_rechecks(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFIND_UPDATES_DIR", str(tmp_path / "updates"))
    monkeypatch.delenv("DEEPFIND_UPDATE_REPO", raising=False)
    monkeypatch.setattr(up, "ROOT", tmp_path)                  # no update_source.json here
    monkeypatch.setattr(up, "read_version", lambda: "0.3.0")
    monkeypatch.setattr(up, "self_update_blocker", lambda: "")
    u = up.Updater()
    assert u.info()["configured"] is False
    u.latest, u.state, u.checked_at = {"version": "9.9.9"}, "available", 123.0
    info = u.set_repo("someone/deepfind")
    assert info["repo"] == "someone/deepfind" and info["repo_custom"] is True and info["configured"] is True
    assert info["available"] is False and info["state"] == "idle" and u.checked_at == 0.0
    with pytest.raises(up.UpdateError):
        u.set_repo("not a repo")
    u.state = "downloading"
    with pytest.raises(up.UpdateError):
        u.set_repo("other/repo")
