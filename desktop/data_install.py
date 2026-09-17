"""Resumable national OSM download and isolated, atomically activated imports."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event
from urllib.request import Request, urlopen
from uuid import uuid4

OSM_URL = "https://download.geofabrik.de/asia/china-latest.osm.pbf"
REQUIRED_FILES = (
    "china_metro_routes.geojson",
    "china_metro_stations.geojson",
    "china_metro_station_areas.geojson",
    "china_metro_route_catalog.json",
    "china_metro_import_manifest.json",
    "china_metro_construction.geojson",
    "china_metro_construction_manifest.json",
    "china_metro_station_area_manifest.json",
)


class Cancelled(Exception):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def active_directory(root):
    base = Path(root) / "data/processed/osm"
    pointer = base / "active_dataset.json"
    if not pointer.is_file():
        return base
    value = json.loads(pointer.read_text(encoding="utf-8"))["dataset"]
    candidate = (base / value).resolve()
    if candidate.parent != (base / "datasets").resolve() or not candidate.is_dir():
        raise ValueError(
            "地铁数据目录指针无效，请检查 data/processed/osm/active_dataset.json"
        )
    return candidate


def download_file(url, destination, progress, cancel, force=False, opener=urlopen):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and not force:
        progress({"phase": "cached", "message": "复用已下载的全国 PBF（未请求更新）"})
        return destination
    with opener(
        Request(url, method="HEAD", headers={"User-Agent": "RailScope/0.1"}), timeout=30
    ) as head:
        total = int(head.headers.get("Content-Length", 0))
        version = head.headers.get("ETag") or head.headers.get("Last-Modified")
    if not total or not version:
        raise RuntimeError("数据源未提供文件大小或版本，不能安全下载，请稍后重试")
    with opener(
        Request(url + ".md5", headers={"User-Agent": "RailScope/0.1"}), timeout=30
    ) as response:
        expected = response.read(4096).decode("ascii").split()[0].lower()
    if len(expected) != 32 or any(c not in "0123456789abcdef" for c in expected):
        raise RuntimeError("数据源校验信息无效，旧数据未修改")
    partial = destination.with_suffix(destination.suffix + ".part")
    meta = partial.with_suffix(partial.suffix + ".json")
    identity = {"url": url, "version": version, "total": total, "md5": expected}
    previous = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    offset = partial.stat().st_size if partial.exists() and previous == identity else 0
    if offset > total:
        offset = 0
    free = shutil.disk_usage(destination.parent).free
    if free < total - offset + 64 * 1024 * 1024:
        raise RuntimeError("磁盘空间不足，下载至少需要约 1.5 GB；导入还需要额外空间")
    atomic_json(meta, identity)
    headers = {"User-Agent": "RailScope/0.1", "Accept-Encoding": "identity"}
    if offset and offset < total:
        headers.update({"Range": f"bytes={offset}-", "If-Range": version})
    if offset < total:
        with opener(Request(url, headers=headers), timeout=30) as response:
            if offset and (
                response.status != 206
                or not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                )
            ):
                offset = 0
            if response.status == 206 and not response.headers.get(
                "Content-Range", ""
            ).startswith(f"bytes {offset}-"):
                raise RuntimeError("服务器返回的续传位置不一致，下载未应用")
            with partial.open("ab" if offset else "wb") as handle:
                received = offset
                while True:
                    if cancel.is_set():
                        raise Cancelled("下载已暂停；再次开始将继续未完成的下载")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    progress(
                        {
                            "phase": "download",
                            "received": received,
                            "total": total,
                            "message": "下载全国 OpenStreetMap 数据",
                        }
                    )
    if partial.stat().st_size != total:
        raise RuntimeError("文件尚未完整下载；再次开始可以续传，旧文件未替换")
    progress({"phase": "verify", "message": "校验完整 PBF 文件，避免使用损坏的数据"})
    digest = hashlib.md5()
    with partial.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            if cancel.is_set():
                raise Cancelled("已暂停文件校验，完整下载仍然保留")
            digest.update(chunk)
    if digest.hexdigest() != expected:
        atomic_json(meta, {**identity, "invalid": True})
        raise RuntimeError("下载文件校验失败；旧文件未替换，下次会重新下载")
    partial.replace(destination)
    meta.unlink(missing_ok=True)
    atomic_json(destination.with_suffix(destination.suffix + ".json"), identity)
    return destination


def activate_dataset(root, stage):
    root, stage = Path(root).resolve(), Path(stage).resolve()
    base = root / "data/processed/osm"
    if stage.parent != (base / "datasets").resolve():
        raise ValueError("导入目录必须在本项目 datasets 中")
    for name in REQUIRED_FILES:
        payload = json.loads((stage / name).read_text(encoding="utf-8"))
        if name.endswith(".geojson") and payload.get("type") != "FeatureCollection":
            raise ValueError(f"图层格式无效：{name}")
    catalog = json.loads(
        (stage / "china_metro_route_catalog.json").read_text(encoding="utf-8")
    )
    if not catalog.get("routes"):
        raise ValueError("未找到地铁线路；旧数据未替换")
    pointer = base / "active_dataset.json"
    if pointer.exists():
        shutil.copy2(
            pointer, pointer.with_name("active_dataset." + uuid4().hex + ".bak")
        )
    atomic_json(
        pointer,
        {
            "dataset": "datasets/" + stage.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": OSM_URL,
        },
    )


def install(root, progress, cancel=None, force=False, pbf=None):
    root = Path(root).resolve()
    cancel = cancel or Event()
    if pbf is None:
        pbf = download_file(
            OSM_URL, root / "data/raw/osm/china-latest.osm.pbf", progress, cancel, force
        )
    if cancel.is_set():
        raise Cancelled("已暂停，尚未开始导入")
    base = root / "data/processed/osm/datasets"
    base.mkdir(parents=True, exist_ok=True)
    # File-backed national node indexes may temporarily occupy several GB.
    if shutil.disk_usage(base).free < 8 * 1024**3:
        raise RuntimeError("导入需要至少 8 GB 可用磁盘空间；PBF 已保留，无须重复下载")
    stage = base / uuid4().hex
    stage.mkdir()
    logs = root / "data/logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / ("metro-install-" + stage.name + ".log")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    env["PYTHONPATH"] = str(root / "backend") + os.pathsep + env.get("PYTHONPATH", "")
    commands = (
        (
            "导入全国运营线路、颜色、属性及站点",
            [
                sys.executable,
                "-m",
                "railscope.cli",
                "metro",
                "import",
                str(pbf),
                "--output",
                str(stage),
            ],
        ),
        (
            "导入在建地铁工程",
            [
                sys.executable,
                "-m",
                "railscope.cli",
                "metro",
                "construction",
                str(pbf),
                "--output",
                str(stage),
            ],
        ),
        (
            "提取真实车站范围（含多面关系）",
            [
                sys.executable,
                str(root / "desktop/import_station_areas.py"),
                "--pbf",
                str(pbf),
                "--output",
                str(stage / "china_metro_station_areas.geojson"),
                "--worker",
            ],
        ),
    )
    for step, (message, command) in enumerate(commands, 1):
        progress(
            {
                "phase": "import",
                "message": message,
                "step": step,
                "steps": 3,
                "log": str(log),
            }
        )
        with log.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
            subprocess.run(
                command,
                cwd=root,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0,
            )
        # Each child has exited, so its mapping is no longer locked on Windows.
        for index in stage.glob("*.idx"):
            index.unlink()
    manifest_path = stage / "china_metro_station_area_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["temporary_index"] = None
    atomic_json(manifest_path, manifest)
    activate_dataset(root, stage)
    progress(
        {
            "phase": "done",
            "message": "全国地铁数据已就绪。保存运行计划后重启软件加载；原数据和用户设置仍保留。",
            "directory": str(stage),
            "log": str(log),
        }
    )
    return stage
