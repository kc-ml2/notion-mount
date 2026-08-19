from __future__ import annotations

import errno
import os
from pathlib import Path


def mount_readonly(source: Path, mountpoint: Path, foreground: bool = True) -> None:
    try:
        from fuse import FUSE, FuseOSError, Operations
    except ImportError as error:
        raise RuntimeError("Install mount support with: pip install 'notion-mount[fuse]'") from error

    root = source.resolve()

    class ReadOnlyView(Operations):  # type: ignore[misc]
        def _path(self, path: str) -> Path:
            candidate = (root / path.lstrip("/")).resolve()
            if not candidate.is_relative_to(root) or candidate == root / ".notion-mount":
                raise FuseOSError(errno.ENOENT)
            return candidate

        def getattr(self, path: str, fh: int | None = None) -> dict[str, int | float]:
            try:
                stat = self._path(path).lstat()
            except FileNotFoundError:
                raise FuseOSError(errno.ENOENT)
            return {key: getattr(stat, key) for key in (
                "st_atime", "st_ctime", "st_gid", "st_mode", "st_mtime",
                "st_nlink", "st_size", "st_uid",
            )}

        def readdir(self, path: str, fh: int) -> list[str]:
            try:
                names = [item.name for item in self._path(path).iterdir()]
            except FileNotFoundError:
                raise FuseOSError(errno.ENOENT)
            return [".", "..", *(name for name in names if name != ".notion-mount")]

        def open(self, path: str, flags: int) -> int:
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
                raise FuseOSError(errno.EROFS)
            try:
                return os.open(self._path(path), os.O_RDONLY)
            except FileNotFoundError:
                raise FuseOSError(errno.ENOENT)

        def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
            return os.pread(fh, size, offset)

        def release(self, path: str, fh: int) -> None:
            os.close(fh)

        def access(self, path: str, mode: int) -> int:
            if mode & os.W_OK:
                raise FuseOSError(errno.EROFS)
            if not self._path(path).exists():
                raise FuseOSError(errno.ENOENT)
            return 0

        def statfs(self, path: str) -> dict[str, int]:
            stat = os.statvfs(root)
            return {key: getattr(stat, key) for key in (
                "f_bavail", "f_bfree", "f_blocks", "f_bsize", "f_favail",
                "f_ffree", "f_files", "f_flag", "f_frsize", "f_namemax",
            )}

    mountpoint.mkdir(parents=True, exist_ok=True)
    FUSE(ReadOnlyView(), str(mountpoint), foreground=foreground, ro=True, nothreads=True)
