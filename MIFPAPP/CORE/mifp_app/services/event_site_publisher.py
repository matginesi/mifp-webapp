"""Small, explicit backends for publishing validated event-site trees.

Callers must validate and extract the untrusted ZIP before invoking a publisher.
Publishers only receive a private directory tree and a validated relative
publication path; they never derive credentials or remote paths from a URL.
"""
from __future__ import annotations

import ftplib
import os
import shutil
import ssl
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4


class PublicationError(RuntimeError):
    """A safe, operator-facing publication failure without secret details."""


@dataclass(frozen=True)
class PublisherStatus:
    backend: str
    available: bool
    message: str


def safe_publication_path(value: str) -> str:
    supplied = str(value or "").strip().replace("\\", "/")
    if supplied.startswith("/"):
        raise ValueError("Publication path must be relative.")
    raw = supplied.strip("/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or len(path.parts) > 4
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(not part[0].isalnum() for part in path.parts)
        or any(not all(ch.isalnum() or ch in "._~-" for ch in part) for part in path.parts)
    ):
        raise ValueError("Publication path contains unsupported characters or segments.")
    return path.as_posix()


class EventSitePublisher:
    backend = "unknown"

    def status(self) -> PublisherStatus:
        raise NotImplementedError

    def publish(
        self, source: Path, destination: str, *, replace: bool, keep_rollback: bool
    ) -> None:
        raise NotImplementedError

    def restore_previous(self, destination: str) -> None:
        raise PublicationError("This publication backend has no retained rollback copy.")


class DisabledEventSitePublisher(EventSitePublisher):
    backend = "disabled"

    def __init__(self, reason: str = "Event-site publication is not configured.") -> None:
        self.reason = reason

    def status(self) -> PublisherStatus:
        return PublisherStatus(self.backend, False, self.reason)

    def publish(self, source: Path, destination: str, *, replace: bool, keep_rollback: bool) -> None:
        del source, destination, replace, keep_rollback
        raise PublicationError(self.reason)


class LocalEventSitePublisher(EventSitePublisher):
    backend = "local"

    def __init__(self, root: Path, *, backend: str = "local") -> None:
        self.root = Path(root)
        self.backend = backend

    def status(self) -> PublisherStatus:
        return PublisherStatus(self.backend, True, f"Local development publisher: {self.root}")

    def _target(self, destination: str) -> Path:
        relative = safe_publication_path(destination)
        root = self.root.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:  # pragma: no cover - defense in depth
            raise ValueError("Publication destination escapes the local publisher root.") from exc
        return target

    def publish(
        self, source: Path, destination: str, *, replace: bool, keep_rollback: bool
    ) -> None:
        source = Path(source)
        if not source.is_dir() or source.is_symlink():
            raise PublicationError("Validated event-site staging is unavailable.")
        target = self._target(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and (target.is_symlink() or not target.is_dir()):
            raise PublicationError("Existing publication destination is unsafe.")
        if target.exists() and not replace:
            raise PublicationError("Destination already exists; select atomic replace/update.")
        stage = target.parent / f".{target.name}.stage-{uuid4().hex}"
        rollback = target.parent / f".{target.name}.rollback"
        old_rollback = target.parent / f".{target.name}.rollback-old-{uuid4().hex}"
        moved_old = False
        try:
            shutil.copytree(source, stage, copy_function=shutil.copy2)
            if target.exists():
                if rollback.exists():
                    if rollback.is_symlink() or not rollback.is_dir():
                        raise PublicationError("Rollback location is unsafe.")
                    rollback.rename(old_rollback)
                target.rename(rollback)
                moved_old = True
            stage.rename(target)
            if moved_old and not keep_rollback:
                shutil.rmtree(rollback)
            if old_rollback.exists():
                shutil.rmtree(old_rollback)
        except Exception:
            if target.exists() and moved_old:
                shutil.rmtree(target, ignore_errors=True)
            if moved_old and rollback.exists() and not target.exists():
                rollback.rename(target)
            if old_rollback.exists() and not rollback.exists():
                old_rollback.rename(rollback)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def restore_previous(self, destination: str) -> None:
        target = self._target(destination)
        rollback = target.parent / f".{target.name}.rollback"
        if not target.is_dir() or target.is_symlink() or not rollback.is_dir() or rollback.is_symlink():
            raise PublicationError("No safe retained website version is available.")
        swap = target.parent / f".{target.name}.restore-{uuid4().hex}"
        target.rename(swap)
        try:
            rollback.rename(target)
            swap.rename(rollback)
        except Exception:
            if target.exists() and not rollback.exists():
                target.rename(rollback)
            if swap.exists() and not target.exists():
                swap.rename(target)
            raise


class FtpsEventSitePublisher(EventSitePublisher):
    """Explicit-TLS FTPS publisher using same-parent directory renames.

    FTP has no portable atomic-replace primitive. On servers (including common
    shared hosting) where directory rename is atomic, the visible switch is
    atomic; otherwise the old tree is restored after a failed switch whenever
    the server permits it.
    """

    backend = "ftps"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        remote_root: str,
        timeout: int = 30,
        client_factory: Callable[[], ftplib.FTP_TLS] | None = None,
        backend: str = "ftps",
    ) -> None:
        self.host = host.strip()
        self.port = int(port)
        self.username = username
        self.password = password
        self.remote_root = self._safe_root(remote_root)
        self.timeout = int(timeout)
        self.backend = backend
        self.client_factory = client_factory or (
            lambda: ftplib.FTP_TLS(context=ssl.create_default_context())
        )

    @staticmethod
    def _safe_root(value: str) -> str:
        value = str(value or "").strip().replace("\\", "/")
        if not value:
            return ""
        if "\x00" in value or any(part == ".." for part in value.split("/")):
            raise ValueError("EVENTS_REMOTE_ROOT must be a safe FTPS directory.")
        return value.rstrip("/") or "/"

    def status(self) -> PublisherStatus:
        missing = [
            name for name, value in (
                ("EVENTS_REMOTE_HOST", self.host),
                ("EVENTS_REMOTE_USER", self.username),
                ("EVENTS_REMOTE_PASSWORD", self.password),
                ("EVENTS_REMOTE_ROOT", self.remote_root),
            ) if not value
        ]
        if missing:
            return PublisherStatus(self.backend, False, "Missing " + ", ".join(missing))
        return PublisherStatus(self.backend, True, "FTPS publisher configured (connection not probed)")

    def _connect(self) -> ftplib.FTP_TLS:
        if not self.status().available:
            raise PublicationError(self.status().message)
        try:
            client = self.client_factory()
            client.connect(self.host, self.port, timeout=self.timeout)
            client.auth()
            client.prot_p()
            client.login(self.username, self.password)
            return client
        except Exception as exc:
            raise PublicationError(
                f"FTPS connection failed ({type(exc).__name__}); the previous website was not changed."
            ) from None

    @staticmethod
    def _join(base: str, relative: str) -> str:
        return f"{base.rstrip('/')}/{relative.strip('/')}" if base != "/" else f"/{relative.strip('/')}"

    @staticmethod
    def _exists(client: ftplib.FTP_TLS, path: str) -> bool:
        current = client.pwd()
        try:
            client.cwd(path)
            return True
        except ftplib.error_perm:
            return False
        finally:
            client.cwd(current)

    @staticmethod
    def _mkdirs(client: ftplib.FTP_TLS, path: str) -> None:
        absolute = path.startswith("/")
        current = client.pwd()
        try:
            if absolute:
                client.cwd("/")
            for part in PurePosixPath(path).parts:
                if part == "/":
                    continue
                try:
                    client.cwd(part)
                except ftplib.error_perm:
                    client.mkd(part)
                    client.cwd(part)
        finally:
            client.cwd(current)

    @classmethod
    def _delete_tree(cls, client: ftplib.FTP_TLS, path: str) -> None:
        current = client.pwd()
        client.cwd(path)
        try:
            entries = list(client.mlsd())
            for name, facts in entries:
                if name in {".", ".."}:
                    continue
                child = cls._join(path, name)
                if facts.get("type") == "dir":
                    cls._delete_tree(client, child)
                else:
                    client.delete(child)
        finally:
            client.cwd(current)
        client.rmd(path)

    @classmethod
    def _upload_tree(cls, client: ftplib.FTP_TLS, source: Path, remote: str) -> None:
        cls._mkdirs(client, remote)
        for directory in sorted(path for path in source.rglob("*") if path.is_dir()):
            cls._mkdirs(client, cls._join(remote, directory.relative_to(source).as_posix()))
        for path in sorted(path for path in source.rglob("*") if path.is_file()):
            relative = path.relative_to(source).as_posix()
            remote_file = cls._join(remote, relative)
            with path.open("rb") as stream:
                client.storbinary(f"STOR {remote_file}", stream)
            size = client.size(remote_file)
            if size is not None and int(size) != path.stat().st_size:
                raise OSError(f"remote size mismatch for {relative}")

    def publish(
        self, source: Path, destination: str, *, replace: bool, keep_rollback: bool
    ) -> None:
        relative = safe_publication_path(destination)
        final = self._join(self.remote_root, relative)
        parent, leaf = final.rsplit("/", 1)
        parent = parent or "/"
        stage = self._join(parent, f".{leaf}.stage-{uuid4().hex}")
        rollback = self._join(parent, f".{leaf}.rollback")
        old_rollback = self._join(parent, f".{leaf}.rollback-old-{uuid4().hex}")
        client = self._connect()
        moved_old = False
        try:
            self._mkdirs(client, parent)
            exists = self._exists(client, final)
            if exists and not replace:
                raise PublicationError("Destination already exists; select staged replace/update.")
            self._upload_tree(client, Path(source), stage)
            if exists:
                if self._exists(client, rollback):
                    client.rename(rollback, old_rollback)
                client.rename(final, rollback)
                moved_old = True
            client.rename(stage, final)
            if moved_old and not keep_rollback:
                self._delete_tree(client, rollback)
            if self._exists(client, old_rollback):
                self._delete_tree(client, old_rollback)
        except PublicationError:
            raise
        except Exception as exc:
            try:
                if moved_old and not self._exists(client, final) and self._exists(client, rollback):
                    client.rename(rollback, final)
            except Exception:
                pass
            raise PublicationError(
                f"FTPS publication failed ({type(exc).__name__}); the previous website was retained when the host allowed rollback."
            ) from None
        finally:
            try:
                if self._exists(client, stage):
                    self._delete_tree(client, stage)
                client.quit()
            except Exception:
                try:
                    client.close()
                except Exception:
                    pass

    def restore_previous(self, destination: str) -> None:
        relative = safe_publication_path(destination)
        final = self._join(self.remote_root, relative)
        parent, leaf = final.rsplit("/", 1)
        rollback = self._join(parent or "/", f".{leaf}.rollback")
        swap = self._join(parent or "/", f".{leaf}.restore-{uuid4().hex}")
        client = self._connect()
        try:
            if not self._exists(client, final) or not self._exists(client, rollback):
                raise PublicationError("No retained remote website version is available.")
            client.rename(final, swap)
            try:
                client.rename(rollback, final)
                client.rename(swap, rollback)
            except Exception:
                if self._exists(client, swap) and not self._exists(client, final):
                    client.rename(swap, final)
                raise
        except PublicationError:
            raise
        except Exception as exc:
            raise PublicationError(f"FTPS restore failed ({type(exc).__name__}).") from None
        finally:
            try:
                client.quit()
            except Exception:
                client.close()


def publisher_from_config(config) -> EventSitePublisher:
    backend = str(config.get("EVENTS_PUBLISH_BACKEND", "disabled")).strip().lower()
    if backend in {"local", "local-vps"}:
        return LocalEventSitePublisher(Path(config["EVENTS_LOCAL_ROOT"]), backend=backend)
    if backend in {"remote", "ftps"}:
        protocol = str(config.get("EVENTS_REMOTE_PROTOCOL", "ftps")).strip().lower()
        if protocol != "ftps":
            return DisabledEventSitePublisher(f"Unsupported remote event protocol: {protocol}")
        try:
            return FtpsEventSitePublisher(
                host=str(config.get("EVENTS_REMOTE_HOST", "")),
                port=int(config.get("EVENTS_REMOTE_PORT", 21)),
                username=str(config.get("EVENTS_REMOTE_USER", "")),
                password=str(config.get("EVENTS_REMOTE_PASSWORD", "")),
                remote_root=str(config.get("EVENTS_REMOTE_ROOT", "")),
                timeout=int(config.get("EVENTS_REMOTE_TIMEOUT", 30)),
                backend="remote" if backend == "remote" else "ftps",
            )
        except (TypeError, ValueError) as exc:
            return DisabledEventSitePublisher(f"Invalid FTPS publisher configuration: {exc}")
    if backend == "disabled":
        return DisabledEventSitePublisher()
    return DisabledEventSitePublisher(f"Unknown event publisher backend: {backend}")
