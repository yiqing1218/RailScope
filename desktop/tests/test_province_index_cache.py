import json
import os

from desktop.provinces import province_index, ProvinceIndex


def test_shared_boundaries_build_once_and_refresh_on_file_change(tmp_path, monkeypatch):
    import desktop.provinces as module
    path = tmp_path / 'boundary.json'
    data = {'features': [{'properties': {'shapeName': 'Shanghai'}, 'geometry': {
        'type': 'Polygon', 'coordinates': [[[120, 30], [122, 30], [122, 32], [120, 32], [120, 30]]]}}]}
    path.write_text(json.dumps(data), encoding='utf-8')
    calls = []
    monkeypatch.setattr(module, 'ProvinceIndex', lambda data: calls.append(True) or ProvinceIndex(data))
    a = province_index(path)
    assert province_index(path) is a and len(calls) == 1
    assert a.locate((121, 31)) == '上海市'
    data['features'][0]['properties']['shapeName'] = 'Zhejiang'
    stamp = path.stat().st_mtime_ns
    path.write_text(json.dumps(data), encoding='utf-8')
    # Fast writes can share a Windows timestamp; model a new file snapshot.
    os.utime(path, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
    b = province_index(path)
    assert b is not a and b.locate((121, 31)) == '浙江省'
    assert a.locate((121, 31)) == '上海市' and len(calls) == 2
