"""Prepare the pinned map assets without requiring Node.js or npm."""

import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def ensure_assets(root=ROOT):
    root = Path(root)
    files = ("maplibre-gl.js", "maplibre-gl.css")
    vendor = root / "desktop/vendor"
    if all((vendor / name).is_file() for name in files):
        return vendor
    package = json.loads(
        (root / "frontend/package-lock.json").read_text(encoding="utf-8")
    )["packages"]["node_modules/maplibre-gl"]
    if not package["resolved"].startswith("https://registry.npmjs.org/maplibre-gl/"):
        raise RuntimeError("Unexpected map asset source")
    print("Preparing pinned MapLibre map assets...", flush=True)
    with urlopen(package["resolved"], timeout=60) as response:
        content = response.read(30 * 1024 * 1024 + 1)
    if len(content) > 30 * 1024 * 1024:
        raise RuntimeError("Map package exceeds allowed size")
    actual = "sha512-" + base64.b64encode(hashlib.sha512(content).digest()).decode()
    if actual != package["integrity"]:
        raise RuntimeError("Map package integrity check failed; assets not installed")
    vendor.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        for name in files:
            member = archive.extractfile("package/dist/" + name)
            if member is None:
                raise RuntimeError("Required map asset is missing")
            temporary = vendor / (name + ".tmp")
            temporary.write_bytes(member.read())
            temporary.replace(vendor / name)
        for name in ("LICENSE.txt", "LICENSE"):
            try:
                member = archive.extractfile("package/" + name)
            except KeyError:
                continue
            if member:
                (vendor / "LICENSE.txt").write_bytes(member.read())
                break
    return vendor


if __name__ == "__main__":
    ensure_assets()
    print("Map assets ready.", flush=True)
