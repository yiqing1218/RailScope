"""Prepare the pinned map assets without requiring Node.js or npm."""

import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def ensure_assets(root=ROOT, opener=urlopen):
    root = Path(root)
    files = (
        "maplibre-gl.mjs",
        "maplibre-gl-shared.mjs",
        "maplibre-gl-worker.mjs",
        "maplibre-gl.css",
    )
    vendor = root / "desktop/vendor"
    package = json.loads(
        (root / "frontend/package-lock.json").read_text(encoding="utf-8")
    )["packages"]["node_modules/maplibre-gl"]
    expected_manifest = {
        "version": package["version"],
        "resolved": package["resolved"],
        "integrity": package["integrity"],
    }
    manifest_path = vendor / "manifest.json"
    try:
        cached_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        cached_manifest = None
    if cached_manifest == expected_manifest and all(
        (vendor / name).is_file() for name in files
    ):
        return vendor
    if not package["resolved"].startswith("https://registry.npmjs.org/maplibre-gl/"):
        raise RuntimeError("Unexpected map asset source")
    print("Preparing pinned MapLibre map assets...", flush=True)
    with opener(package["resolved"], timeout=60) as response:
        content = response.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024:
        raise RuntimeError("Map package exceeds allowed size")
    actual = "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode()
    if actual != package["integrity"]:
        raise RuntimeError("Map package integrity check failed; assets not installed")
    extracted = {}
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for name in files:
            member = archive.extractfile("package/dist/" + name)
            if member is None:
                raise RuntimeError("Required map asset is missing")
            extracted[name] = member.read()
        for name in ("LICENSE.txt", "LICENSE"):
            try:
                member = archive.extractfile("package/" + name)
            except KeyError:
                continue
            if member:
                extracted["LICENSE.txt"] = member.read()
                break
    vendor.mkdir(parents=True, exist_ok=True)
    for name, payload in extracted.items():
        temporary = vendor / (name + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(vendor / name)
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(expected_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_manifest.replace(manifest_path)
    return vendor


if __name__ == "__main__":
    ensure_assets()
    print("Map assets ready.", flush=True)
