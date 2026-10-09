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


def test_partially_open_ftps_connection_is_closed_on_login_failure(tmp_path):
    class LoginFailure(FakeFtps):
        closed = False

        def login(self, username, password):
            del username, password
            raise OSError("login failed")

        def close(self):
            self.closed = True

    fake = LoginFailure(tmp_path / "remote")
    publisher = FtpsEventSitePublisher(
        host="hosting.example", port=21, username="user", password="secret",
        remote_root="/events", client_factory=lambda: fake,
    )

    with pytest.raises(PublicationError, match="connection failed"):
        publisher.publish(_source(tmp_path / "site", "new"), "Event-2027", replace=False, keep_rollback=False)

    assert fake.closed is True


@pytest.mark.parametrize("backend, expected", [
    ("local", "Local development publisher"),
    ("local-vps", "Caddy on the same VPS"),
])
def test_local_publisher_description(tmp_path, backend, expected):
    status = LocalEventSitePublisher(tmp_path, backend=backend).status()
    assert status.backend == backend
    assert expected in status.message
    if backend == "local-vps":
        assert "development" not in status.message
        assert str(tmp_path) not in status.message


@pytest.mark.parametrize("destination", ["Event-2027", "Event-2027/regform", "Event-2027/regform/sub"])
def test_vps_approval_blocks_publish_and_restore_until_revoked(tmp_path, destination):
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "lock").touch()
    protected = policy / "protected-paths"
    protected.write_text("")
    root = tmp_path / "public"
    publisher = LocalEventSitePublisher(root, backend="local-vps", policy_root=policy)
    publisher.publish(_source(tmp_path / "v1", "old"), destination, replace=False, keep_rollback=True)
    publisher.publish(_source(tmp_path / "v2", "current"), destination, replace=True, keep_rollback=True)
    protected.write_text("Event-2027/regform\n")
    with pytest.raises(PublicationError, match="events-php-disable"):
        publisher.publish(tmp_path / "v1", destination, replace=True, keep_rollback=True)
    with pytest.raises(PublicationError, match="events-php-disable"):
        publisher.restore_previous(destination)
    assert (root / destination / "index.html").read_text() == "current"
    protected.write_text("")
    publisher.restore_previous(destination)
    assert (root / destination / "index.html").read_text() == "old"
    publisher.publish(tmp_path / "v2", destination, replace=True, keep_rollback=True)
    assert (root / destination / "index.html").read_text() == "current"


@pytest.mark.parametrize("contents", [None, "../unsafe\n", "\n"])
def test_vps_missing_or_invalid_policy_fails_closed(tmp_path, contents):
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "lock").touch()
    if contents is not None:
        (policy / "protected-paths").write_text(contents)
    publisher = LocalEventSitePublisher(tmp_path / "public", backend="local-vps", policy_root=policy)
    with pytest.raises(PublicationError, match="policy unavailable or invalid"):
        publisher.publish(_source(tmp_path / "source", "new"), "Event-2027", replace=True, keep_rollback=True)
    assert not publisher.root.exists()


def test_vps_policy_blocks_missing_approved_target_but_allows_unrelated_site(tmp_path):
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "lock").touch()
    (policy / "protected-paths").write_text("Event-2027/regform\n")
    publisher = LocalEventSitePublisher(tmp_path / "public", backend="local-vps", policy_root=policy)
    source = _source(tmp_path / "source", "new")
    with pytest.raises(PublicationError, match="approved PHP path"):
        publisher.publish(source, "Event-2027", replace=False, keep_rollback=True)
    publisher.publish(source, "Event-20270", replace=False, keep_rollback=True)
    assert (publisher.root / "Event-20270/index.html").read_text() == "new"


def test_vps_publication_holds_host_interlock_during_copy(tmp_path, monkeypatch):
    import subprocess
    import sys
    import shutil

    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "lock").touch()
    (policy / "protected-paths").write_text("")
    publisher = LocalEventSitePublisher(tmp_path / "public", backend="local-vps", policy_root=policy)
    copytree = shutil.copytree

    def checked_copy(*args, **kwargs):
        result = subprocess.run([sys.executable, "-c", """
import fcntl, sys
with open(sys.argv[1], 'rb') as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(23)
""", str(policy / "lock")], check=False)
        assert result.returncode == 23
        return copytree(*args, **kwargs)

    monkeypatch.setattr("mifp_app.services.event_site_publisher.shutil.copytree", checked_copy)
    publisher.publish(_source(tmp_path / "source", "new"), "Event", replace=False, keep_rollback=True)


@pytest.mark.parametrize("failure", ["copy", "switch"])
def test_failed_vps_replacement_after_revocation_keeps_php_unapproved(tmp_path, monkeypatch, failure):
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "lock").touch()
    (policy / "protected-paths").write_text("")
    root = tmp_path / "public"
    publisher = LocalEventSitePublisher(root, backend="local-vps", policy_root=policy)
    old = _source(tmp_path / "old", "old")
    (old / "index.php").write_text("<?php echo 'old';")
    new = _source(tmp_path / "new", "new")
    (new / "index.php").write_text("<?php echo 'new';")
    publisher.publish(old, "Event", replace=False, keep_rollback=True)
    if failure == "copy":
        def interrupted(*_args, **_kwargs):
            raise OSError("interrupted copy")
        monkeypatch.setattr("mifp_app.services.event_site_publisher.shutil.copytree", interrupted)
    else:
        rename = Path.rename
        def interrupted(path, target):
            if ".stage-" in path.name:
                raise OSError("interrupted switch")
            return rename(path, target)
        monkeypatch.setattr(Path, "rename", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        publisher.publish(new, "Event", replace=True, keep_rollback=True)
    assert (root / "Event/index.php").read_text() == "<?php echo 'old';"
    assert (policy / "protected-paths").read_text() == ""
    assert not list(root.glob(".*.stage-*"))


def test_rollback_cleanup_failure_does_not_remove_successful_publication(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / "public"
    publisher = LocalEventSitePublisher(root)
    publisher.publish(_source(tmp_path / "v1", "v1"), "Event", replace=False, keep_rollback=True)
    publisher.publish(_source(tmp_path / "v2", "v2"), "Event", replace=True, keep_rollback=True)
    rmtree = shutil.rmtree

    def failed_cleanup(path, *args, **kwargs):
        if ".rollback-old-" in path.name:
            if kwargs.get("ignore_errors"):
                return
            raise OSError("rollback cleanup denied")
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr("mifp_app.services.event_site_publisher.shutil.rmtree", failed_cleanup)
    publisher.publish(_source(tmp_path / "v3", "v3"), "Event", replace=True, keep_rollback=False)
    assert (root / "Event/index.html").read_text() == "v3"
