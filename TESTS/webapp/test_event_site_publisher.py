from __future__ import annotations

import ftplib
from pathlib import Path, PurePosixPath

import pytest

from mifp_app.services.event_site_publisher import (
    DisabledEventSitePublisher,
    FtpsEventSitePublisher,
    LocalEventSitePublisher,
    PublicationError,
    safe_publication_path,
)


class FakeFtps:
    def __init__(self, root: Path, *, fail_stage_switch: bool = False):
        self.root = root
        self.current = PurePosixPath("/")
        self.fail_stage_switch = fail_stage_switch

    def _path(self, value: str) -> Path:
        remote = PurePosixPath(value)
        if not remote.is_absolute():
            remote = self.current / remote
        relative = remote.as_posix().lstrip("/")
        return self.root / relative

    def connect(self, host, port, timeout):
        del host, port, timeout

    def auth(self):
        return None

    def prot_p(self):
        return None

    def login(self, username, password):
        del username, password

    def pwd(self):
        return self.current.as_posix()

    def cwd(self, value):
        target = self._path(value)
        if not target.is_dir():
            raise ftplib.error_perm("550 missing")
        remote = PurePosixPath(value)
        self.current = remote if remote.is_absolute() else self.current / remote

    def mkd(self, value):
        self._path(value).mkdir()

    def mlsd(self):
        return [
            (item.name, {"type": "dir" if item.is_dir() else "file"})
            for item in self._path(".").iterdir()
        ]

    def storbinary(self, command, stream):
        target = self._path(command.removeprefix("STOR "))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(stream.read())

    def size(self, value):
        return self._path(value).stat().st_size

    def rename(self, source, destination):
        if self.fail_stage_switch and ".stage-" in source:
            raise OSError("simulated interrupted switch")
        self._path(source).rename(self._path(destination))

    def delete(self, value):
        self._path(value).unlink()

    def rmd(self, value):
        self._path(value).rmdir()

    def quit(self):
        return None

    def close(self):
        return None


def _source(path: Path, text: str) -> Path:
    path.mkdir()
    (path / "index.html").write_text(text, encoding="utf-8")
    (path / "assets").mkdir()
    (path / "assets/site.css").write_text("body{}", encoding="utf-8")
    return path


@pytest.mark.parametrize("value", ["../escape", "/absolute", "safe/../../escape", "bad space"])
def test_publication_path_rejects_escape_and_unsafe_names(value):
    with pytest.raises(ValueError):
        safe_publication_path(value)


def test_local_publish_and_republish_preserve_previous_on_failed_copy(tmp_path, monkeypatch):
    root = tmp_path / "published"
    publisher = LocalEventSitePublisher(root)
    publisher.publish(_source(tmp_path / "v1", "v1"), "Event-2027", replace=False, keep_rollback=True)
    publisher.publish(_source(tmp_path / "v2", "v2"), "Event-2027", replace=True, keep_rollback=True)
    assert (root / "Event-2027/index.html").read_text() == "v2"
    assert (root / ".Event-2027.rollback/index.html").read_text() == "v1"

    def interrupted(*_args, **_kwargs):
        raise OSError("interrupted copy")

    monkeypatch.setattr("mifp_app.services.event_site_publisher.shutil.copytree", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        publisher.publish(tmp_path / "v1", "Event-2027", replace=True, keep_rollback=True)
    assert (root / "Event-2027/index.html").read_text() == "v2"


def test_ftps_uses_tls_staging_and_restores_old_site_when_switch_fails(tmp_path):
    remote = tmp_path / "remote"
    (remote / "events/Event-2027").mkdir(parents=True)
    (remote / "events/Event-2027/index.html").write_text("old")
    fake = FakeFtps(remote, fail_stage_switch=True)
    publisher = FtpsEventSitePublisher(
        host="hosting.example", port=21, username="user", password="secret",
        remote_root="/events", client_factory=lambda: fake,
    )
    with pytest.raises(PublicationError, match="previous website was retained"):
        publisher.publish(_source(tmp_path / "new", "new"), "Event-2027", replace=True, keep_rollback=True)
    assert (remote / "events/Event-2027/index.html").read_text() == "old"
    assert not any("stage-" in item.name for item in (remote / "events").iterdir())


def test_failed_connection_is_sanitized_and_disabled_backend_is_nonfatal(tmp_path):
    def fail_client():
        raise OSError("password=must-not-leak")

    publisher = FtpsEventSitePublisher(
        host="hosting.example", port=21, username="user", password="must-not-leak",
        remote_root="/events", client_factory=fail_client,
    )
    with pytest.raises(PublicationError) as failure:
        publisher.publish(_source(tmp_path / "site", "new"), "Event-2027", replace=True, keep_rollback=True)
    assert "must-not-leak" not in str(failure.value)

    disabled = DisabledEventSitePublisher()
    assert disabled.status().available is False
    with pytest.raises(PublicationError, match="not configured"):
        disabled.publish(tmp_path / "site", "Event-2027", replace=False, keep_rollback=False)
