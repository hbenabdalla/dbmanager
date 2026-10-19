"""Storage providers for backups.

Every provider stores objects under a key (``platform/2026/10/<backup id>/archive.tar.age``)
relative to its root and offers the same small interface: ``put`` a local file, ``get`` it
back, ``size`` (``None`` when missing), ``delete``, ``list`` a prefix and ``check`` (a write /
read / delete probe). Keys are validated so a provider can never be steered outside its root.

* :class:`FilesystemStorage` — local disk, a mounted volume or an NFS mount. The directory
  must be under one of ``DBM_BACKUP_ALLOWED_ROOTS``; writes go to a temporary name and are
  renamed into place, so a crash never leaves a half-written object under the final key.
* :class:`S3Storage` — any S3-compatible service (AWS S3, MinIO, Ceph RGW, …) through boto3.

Azure Blob Storage and Google Cloud Storage are planned (see the roadmap); they are not
implemented.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.crypto import EncryptedBlob, get_keyring

FILESYSTEM_KINDS = ("local", "mounted", "nfs")
KINDS = (*FILESYSTEM_KINDS, "s3")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,1023}$")


class StorageError(Exception):
    pass


def check_key(key: str) -> str:
    parts = PurePosixPath(key).parts
    if not _KEY_RE.match(key) or any(p in ("..", ".") for p in parts) or key.endswith("/"):
        raise StorageError(f"invalid object key {key!r}")
    return key


class StorageProvider(Protocol):
    kind: str

    async def put(self, local: Path, key: str) -> int: ...
    async def get(self, key: str, local: Path) -> None: ...
    async def size(self, key: str) -> int | None: ...
    async def delete(self, key: str) -> None: ...
    async def list(self, prefix: str) -> list[str]: ...
    async def check(self) -> dict[str, Any]: ...
    def describe(self, key: str) -> str: ...


# ---- file system ------------------------------------------------------------------------------
def allowed_roots() -> list[Path]:
    return [Path(r).resolve() for r in get_settings().backup_allowed_roots if r.strip()]


def validate_directory(path: str) -> Path:
    """The resolved directory, if it is inside an allowed root (symbolic links are resolved first)."""
    if not path or not os.path.isabs(path):
        raise StorageError("the directory must be an absolute path")
    resolved = Path(path).resolve()
    for root in allowed_roots():
        if resolved == root or root in resolved.parents:
            return resolved
    roots = ", ".join(str(r) for r in allowed_roots()) or "(none configured)"
    raise StorageError(f"{resolved} is outside the allowed backup directories ({roots}); see DBM_BACKUP_ALLOWED_ROOTS")


def _is_mount(path: Path) -> bool:
    p = path
    while True:
        if p.is_mount():
            return p != Path("/")
        if p.parent == p:
            return False
        p = p.parent


class FilesystemStorage:
    def __init__(self, kind: str, path: str):
        self.kind = kind
        self.root = validate_directory(path)

    def _path(self, key: str) -> Path:
        p = (self.root / check_key(key)).resolve()
        if self.root not in p.parents:
            raise StorageError(f"invalid object key {key!r}")
        return p

    def describe(self, key: str) -> str:
        return str(self.root / key)

    def _put_sync(self, local: Path, key: str) -> int:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            shutil.copyfile(local, tmp)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest.stat().st_size

    async def put(self, local: Path, key: str) -> int:
        try:
            return await asyncio.to_thread(self._put_sync, local, key)
        except OSError as exc:
            raise StorageError(f"cannot write {self.describe(key)}: {exc.strerror or exc}") from exc

    async def get(self, key: str, local: Path) -> None:
        src = self._path(key)
        try:
            await asyncio.to_thread(shutil.copyfile, src, local)
        except FileNotFoundError as exc:
            raise StorageError(f"{self.describe(key)} does not exist") from exc
        except OSError as exc:
            raise StorageError(f"cannot read {self.describe(key)}: {exc.strerror or exc}") from exc

    async def size(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.is_file() else None

    async def delete(self, key: str) -> None:
        p = self._path(key)
        await asyncio.to_thread(p.unlink, True)
        # remove now-empty directories up to (not including) the root
        d = p.parent
        while d != self.root and self.root in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent

    async def list(self, prefix: str) -> list[str]:
        base = self._path(prefix.rstrip("/")) if prefix else self.root
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root)).replace(os.sep, "/")
            for p in base.rglob("*")
            if p.is_file() and not p.name.endswith(".part")
        )

    async def check(self) -> dict[str, Any]:
        info: dict[str, Any] = {"path": str(self.root), "exists": self.root.is_dir()}
        if not info["exists"]:
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                info["created"] = True
            except OSError as exc:
                raise StorageError(f"{self.root} does not exist and cannot be created: {exc.strerror}") from exc
        info["is_mount"] = _is_mount(self.root)
        if self.kind in ("mounted", "nfs") and not info["is_mount"]:
            info["warning"] = "the directory is not on a mounted file system: backups would stay on this host's disk"
        usage = shutil.disk_usage(self.root)
        info["free_bytes"], info["total_bytes"] = usage.free, usage.total
        probe = f"dbm-probe-{uuid.uuid4().hex[:12]}"
        with tempfile.NamedTemporaryFile(delete=False) as t:
            t.write(b"dbmanager storage probe")
        try:
            await self.put(Path(t.name), probe)
            out = Path(t.name + ".back")
            await self.get(probe, out)
            ok = out.read_bytes() == b"dbmanager storage probe"
            out.unlink(missing_ok=True)
            await self.delete(probe)
        finally:
            os.unlink(t.name)
        if not ok:
            raise StorageError("the probe object read back differs from what was written")
        return info


# ---- S3-compatible ----------------------------------------------------------------------------
def _aad(target_id: object) -> str:
    return f"backup-target:{target_id}"


def seal_secret(target_id: object, value: str | None) -> dict[str, Any] | None:
    return get_keyring().encrypt(value, _aad(target_id)).to_dict() if value else None


def open_secret(target_id: object, sealed: dict[str, Any] | None) -> str | None:
    if not sealed:
        return None
    return get_keyring().decrypt(EncryptedBlob.from_dict(sealed), _aad(target_id))


class S3Storage:
    kind = "s3"

    def __init__(self, config: dict[str, Any], secret_key: str | None):
        import boto3
        from botocore.config import Config

        self.bucket = str(config.get("bucket") or "")
        if not self.bucket:
            raise StorageError("an S3 target needs a bucket")
        self.prefix = str(config.get("prefix") or "").strip("/")
        if self.prefix:
            check_key(self.prefix)
        verify: bool | str = bool(config.get("verify_tls", True))
        self._ca_file: str | None = None
        if verify and config.get("ca_pem"):
            fd, self._ca_file = tempfile.mkstemp(suffix=".pem")
            with os.fdopen(fd, "w") as f:
                f.write(str(config["ca_pem"]))
            verify = self._ca_file
        self.client = boto3.client(
            "s3",
            endpoint_url=config.get("endpoint_url") or None,
            region_name=config.get("region") or None,
            aws_access_key_id=config.get("access_key_id") or None,
            aws_secret_access_key=secret_key or None,
            verify=verify,
            config=Config(
                s3={"addressing_style": config.get("addressing_style") or "auto"},
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=10,
                read_timeout=120,
            ),
        )

    def _key(self, key: str) -> str:
        check_key(key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def describe(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._key(key)}"

    async def _call(self, fn: Any, *args: Any, **kw: Any) -> Any:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return await asyncio.to_thread(fn, *args, **kw)
        except ClientError as exc:
            err = exc.response.get("Error", {})
            raise StorageError(f"S3 {err.get('Code', 'error')}: {err.get('Message', exc)}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"S3: {exc}") from exc

    async def put(self, local: Path, key: str) -> int:
        await self._call(self.client.upload_file, str(local), self.bucket, self._key(key))
        return local.stat().st_size

    async def get(self, key: str, local: Path) -> None:
        await self._call(self.client.download_file, self.bucket, self._key(key), str(local))

    async def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            head = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(f"S3: {exc}") from exc
        return int(head["ContentLength"])

    async def delete(self, key: str) -> None:
        await self._call(self.client.delete_object, Bucket=self.bucket, Key=self._key(key))

    async def list(self, prefix: str) -> list[str]:
        if prefix:
            check_key(prefix.rstrip("/"))
        full = (
            (f"{self.prefix}/{prefix}" if self.prefix else prefix)
            if prefix
            else (f"{self.prefix}/" if self.prefix else "")
        )
        keys: list[str] = []
        token: str | None = None
        strip = len(self.prefix) + 1 if self.prefix else 0
        while True:
            kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": full}
            if token:
                kw["ContinuationToken"] = token
            page = await self._call(self.client.list_objects_v2, **kw)
            keys += [o["Key"][strip:] for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return sorted(keys)
            token = page.get("NextContinuationToken")

    async def check(self) -> dict[str, Any]:
        await self._call(self.client.head_bucket, Bucket=self.bucket)
        probe = f"dbm-probe-{uuid.uuid4().hex[:12]}"
        with tempfile.NamedTemporaryFile(delete=False) as t:
            t.write(b"dbmanager storage probe")
        try:
            await self.put(Path(t.name), probe)
            if await self.size(probe) is None:
                raise StorageError("the probe object is not visible after upload")
            await self.delete(probe)
        finally:
            os.unlink(t.name)
        return {"bucket": self.bucket, "prefix": self.prefix}

    def __del__(self) -> None:
        if self._ca_file:
            try:
                os.unlink(self._ca_file)
            except OSError:
                pass


def provider_for(target: Any) -> StorageProvider:
    """A provider for a :class:`~app.models.BackupTarget` row."""
    if target.kind in FILESYSTEM_KINDS:
        return FilesystemStorage(target.kind, str(target.config.get("path") or ""))
    if target.kind == "s3":
        return S3Storage(target.config, open_secret(target.id, target.secret))
    raise StorageError(f"unknown storage target kind {target.kind!r}")
