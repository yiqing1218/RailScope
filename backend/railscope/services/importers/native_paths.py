"""ASCII aliases for libosmium's narrow Windows file API (no PBF copying)."""

import atexit
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4

_caches = {}
_output_aliases = {}


@contextmanager
def native_worker_cache(anchor):
    """Parent owns a unique cache; remove it only after the worker exits.

    Windows can retain osmium's mmap until process exit. A parent-owned cache
    makes that lifetime explicit and avoids leaking multi-GB coordinate files.
    """
    parent = temporary_directory(anchor)
    with tempfile.TemporaryDirectory(prefix='worker-', dir=parent) as name:
        yield Path(name)


def _short(path):
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer)):
        if buffer.value.isascii():
            return Path(buffer.value)
    return None


def temporary_directory(path):
    key = Path(path).anchor
    if key in _caches:
        return _caches[key]
    supplied = os.environ.get("RAILSCOPE_NATIVE_TEMP")
    if supplied:
        cache = Path(supplied)
    else:
        base = Path(tempfile.gettempdir())
        base = _short(base) or base
        if not str(base).isascii():
            base = Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "RailScope-temp"
        candidates = [p for p in Path(path).resolve().parents if str(p).isascii()]
        candidates.append(base)
        cache = None
        for candidate in candidates:
            if candidate.anchor.lower() != key.lower():
                continue
            try:
                # mkdtemp retries PermissionError on Windows up to TMP_MAX:
                # an unwritable C:/Users would look like a hung installer.
                candidate.mkdir(parents=True, exist_ok=True)
                proposed = candidate / ("railscope-native-" + uuid4().hex)
                proposed.mkdir(mode=0o700)
                cache = proposed
                break
            except OSError:
                continue
        if cache is None:
            raise RuntimeError(
                "无法建立同盘英文临时目录；请将项目移到可写的英文路径后重试"
            )
        atexit.register(shutil.rmtree, cache, ignore_errors=True)
    _caches[key] = cache
    return cache


def native_path(path, output=False):
    path = Path(path).resolve()
    # Worker output indices always belong to the parent's disposable cache,
    # including when the project path already happens to be ASCII.
    if output and os.environ.get('RAILSCOPE_NATIVE_TEMP'):
        cache = Path(os.environ['RAILSCOPE_NATIVE_TEMP'])
        cache.mkdir(parents=True, exist_ok=True)
        key = (str(cache.resolve()), str(path))
        if key not in _output_aliases:
            _output_aliases[key] = cache / (uuid4().hex + '.idx')
        return _output_aliases[key]
    if sys.platform != "win32" or str(path).isascii():
        return path
    if output:
        parent = _short(path.parent)
        if parent and path.name.isascii():
            return parent / path.name
    else:
        alias = _short(path)
        if alias:
            return alias
    cache = temporary_directory(path)
    alias = cache / (
        uuid4().hex
        + (".idx" if output else ".osm.pbf" if path.name.endswith(".pbf") else ".osm")
    )
    if not output:
        if not path.is_file():
            raise FileNotFoundError("OSM 源文件不存在")
        try:
            os.link(path, alias)
        except OSError as error:
            raise RuntimeError(
                "无法建立 OSM 英文路径硬链接；请将项目移到英文路径、同一 NTFS 磁盘后重试（下载已保留）"
            ) from error
    return alias
