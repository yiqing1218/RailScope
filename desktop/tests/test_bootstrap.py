import base64
import hashlib
import io
import json
import tarfile

from desktop.bootstrap import ensure_assets


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _package_bytes():
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, content in {
            "package/dist/maplibre-gl.mjs": b"export const version = 'test';",
            "package/dist/maplibre-gl-shared.mjs": b"export const shared = true;",
            "package/dist/maplibre-gl-worker.mjs": b"export const worker = true;",
            "package/dist/maplibre-gl.css": b".maplibregl-map{}",
            "package/LICENSE.txt": b"test license",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return payload.getvalue()


def test_assets_follow_lockfile_and_reuse_matching_cache(tmp_path):
    package = _package_bytes()
    integrity = "sha512-" + base64.b64encode(
        hashlib.sha512(package).digest()
    ).decode()
    lock_path = tmp_path / "frontend/package-lock.json"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/maplibre-gl": {
                        "version": "6.10.0",
                        "resolved": "https://registry.npmjs.org/maplibre-gl/-/maplibre-gl-6.10.0.tgz",
                        "integrity": integrity,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    downloads = []

    def opener(url, timeout):
        downloads.append((url, timeout))
        return _Response(package)

    vendor = ensure_assets(tmp_path, opener=opener)
    assert (vendor / "maplibre-gl.mjs").read_bytes().startswith(b"export const")
    assert (vendor / "maplibre-gl-shared.mjs").is_file()
    assert (vendor / "maplibre-gl-worker.mjs").is_file()
    assert (vendor / "maplibre-gl.css").read_bytes() == b".maplibregl-map{}"
    assert (vendor / "LICENSE.txt").read_bytes() == b"test license"
    assert json.loads((vendor / "manifest.json").read_text(encoding="utf-8")) == {
        "version": "6.10.0",
        "resolved": "https://registry.npmjs.org/maplibre-gl/-/maplibre-gl-6.10.0.tgz",
        "integrity": integrity,
    }
    assert len(downloads) == 1

    ensure_assets(
        tmp_path,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("matching cache should not download")
        ),
    )
