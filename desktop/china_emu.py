"""China-EMU reference facts, layered below user edits and never used as topology."""

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import parse_qs, quote, urljoin, urlsplit
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from railscope.domain import ReferenceProfile
try:
    from .rail_line_terminals import scope_terminals
except ImportError:
    from rail_line_terminals import scope_terminals

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "data/user_settings/china_emu/reference.json"
SHARED_REFERENCE_PATH = ROOT / "data/catalog/china_emu/reference.json"
BASE = "https://china-emu.cn/"
SCHEMA = "railscope.reference-profiles.v1"
LINE_FIELDS = {
    "线路数": "track_count", "供电制式": "power_supply", "轨距": "gauge_mm",
    "计价里程": "pricing_distance_km", "线下设计速度": "civil_design_speed_kmh",
    "线上设计速度": "track_design_speed_kmh", "最高运行速度": "max_speed_kmh",
    "线间距": "track_spacing_m", "到发线长度": "arrival_departure_track_length_m",
    "列控系统": "train_control_system", "轨道类型": "track_structure",
    "曲线半径": "curve_radius_m", "坡度": "gradient",
}
STATION_FIELDS = {
    "站台数": "platform_count", "邻靠站台股道": "platform_track_count",
    "股道数": "track_count", "贯通正线": "through_mainline_count",
    "独立正线": "independent_mainline_count",
}
VEHICLE_FIELDS = {
    "设计速度": "design_speed_kmh", "运营速度": "operating_speed_kmh",
    "全长": "length_m", "宽度": "width_mm", "车体高度": "height_mm",
    "编组": "formation", "装机功率": "installed_power_kw",
    "轮周牵引功率": "traction_power_kw", "最大轴重": "axle_load_t",
    "转向架型号": "bogie_model", "受电弓型号": "pantograph_model",
    "正常运行网压": "supply_voltage_kv", "列控系统": "train_control_system",
    "列车网络": "train_network", "工作温度": "working_temperature",
}
VEHICLE_LABELS = {v: k for k, v in VEHICLE_FIELDS.items()}


def text(element):
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())


def name_key(value, station=False):
    value = re.sub(r"\s+", "", str(value)).casefold()
    return value.removesuffix("站") if station else value


def source_url(url):
    parts = urlsplit(urljoin(BASE, url))
    if parts.scheme != "https" or parts.hostname != "china-emu.cn" or parts.port or parts.username or parts.password:
        raise ValueError("只接受 china-emu.cn 的 HTTPS 资料地址")
    return parts._replace(fragment="").geturl()


def page_links(soup, path):
    return sorted({source_url(a["href"]) for a in soup.select("a[href]")
                   if urlsplit(urljoin(BASE, a["href"])).path.startswith(path)
                   and urlsplit(urljoin(BASE, a["href"])).hostname == "china-emu.cn"})


def pairs(host, fields):
    result = {}
    for para in host.select(".para"):
        # Hidden web fields are not presented facts (often draft/placeholder data).
        if any("hidden" in e.get("class", []) for e in [para, *para.parents] if hasattr(e, "get")):
            continue
        label = text(para.select_one(".para-M"))
        key = next((v for k, v in fields.items() if label == k or label.startswith(k + " ")), None)
        if not key:
            continue
        value_node = para.select_one(".para-N") or para.select_one(".font-mid")
        if value_node:
            copy = BeautifulSoup(str(value_node), "html.parser")
            for hidden in copy.select(".hidden"):
                hidden.decompose()
            value = text(copy)
            if value and value.casefold() not in {"--", "-", "*", "?", "？", "未知", "暂无", "n/a"}:
                result[key] = value
    return result


def common_attributes(scopes):
    if not scopes:
        return {}
    first = scopes[0]["attributes"]
    return {k: v for k, v in first.items()
            if all(s["attributes"].get(k) == v for s in scopes)}


def parse_profile(html, url, retrieved_at=None):
    url = source_url(url)
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h2")
    name = text(title)
    path = urlsplit(url).path
    if not name and path.startswith("/Trains/Model/Detail-"):
        page_title = text(soup.title)
        if " - 动车组列车 - " in page_title:
            name = page_title.split(" - 动车组列车 - ", 1)[0].strip()
    if not name:
        raise ValueError("资料页面缺少对象名称")
    scopes = []
    if path == "/RailRoads/Line/":
        kind = "line"
        for heading in soup.select(".title-label"):
            host = heading.parent.parent
            attrs = pairs(host, LINE_FIELDS)
            if not attrs:
                continue
            header = heading.parent
            span = header.select_one(".text-blue")
            alias = header.select_one(".font-small")
            scopes.append({"name": text(heading), "span": text(span),
                           "aliases": [text(alias)] if text(alias) else [], "attributes": attrs,
                           "source_notes": text(header.select_one(".text-info"))})
        attributes = common_attributes(scopes)
        endpoints=scope_terminals(scopes)
        if endpoints:
            attributes.update(start_terminal=endpoints[0],end_terminal=endpoints[1])
        if len(scopes) > 1:
            # A section's pricing distance is not a whole-line distance, even
            # when independent sections happen to have the same number.
            attributes.pop("pricing_distance_km", None)
    elif path == "/RailRoads/Station/":
        kind = "station"
        locality = title.parent.select_one(".text-muted")
        for i, scale in enumerate(soup.select(".scale")):
            yards = []
            for heading in scale.select(".section-word"):
                label = text(heading)
                if label != "站场规模":
                    host = heading.parent
                    try:
                        from .reference_integration import structured_yard
                    except ImportError:
                        from reference_integration import structured_yard
                    small = host.select_one(".font-small")
                    yards.append(structured_yard({"name": label, "lines": text(small),
                        "line_names": [text(a) for a in small.select('a[href]')] if small else []}))
            numbers = list(dict.fromkeys(text(e) for e in scale.select(
                ".platform-l,.platform-r,.platform-lm,.platform-rm,.platform-cm") if text(e)))
            # Each scale belongs to its own preceding set of line links and date.
            previous_links = scale.find_previous("div", class_="position-r")
            linked = [] if previous_links is None else [text(a) for a in previous_links.select("a[href]")
                                                       if "/RailRoads/Line/" in a["href"]]
            previous_date = scale.find_previous("div", class_="font-mid")
            scopes.append({"name": f"站场资料 {i + 1}", "attributes": pairs(scale, STATION_FIELDS),
                           "yards": yards, "platform_numbers": numbers, "lines": linked,
                           "source_notes": text(previous_date) if previous_date and re.search(r"\d{4}|未|建设|规划", text(previous_date)) else "",
                           "diagram_status": "source_generated_reference"})
        attributes = common_attributes(scopes)
        if len(scopes) > 1:
            for key in STATION_FIELDS.values():
                attributes.pop(key, None)
        attributes["locality"] = text(locality)
        attributes["main_lines"] = "、".join(dict.fromkeys(text(a) for a in soup.select("a[href]")
                                                        if "/RailRoads/Line/" in a["href"]))
        dates = []
        for heading in soup.select(".section-word"):
            if text(heading) == "开业时间":
                value = text(heading.parent.find_next_sibling())
                if re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", value):
                    dates.append(value)
        if dates and len(set(dates)) == 1:
            attributes["commissioning_date"] = dates[0]
        attributes["station_yards"] = "；".join(dict.fromkeys(
            y["name"] + (" · " + y["lines"] if y["lines"] else "") for s in scopes for y in s["yards"]))
        if len(scopes) == 1:
            attributes["platform_numbers"] = "、".join(scopes[0]["platform_numbers"])
    elif path.startswith("/Trains/Model/Detail-"):
        kind = "vehicle"
        attributes = pairs(soup, VEHICLE_FIELDS)
    else:
        raise ValueError("不支持的资料页面类型")
    if not attributes and not scopes:
        raise ValueError("资料页面未找到支持的参数")
    return asdict(ReferenceProfile(
        id="reference/" + str(uuid5(NAMESPACE_URL, url)), kind=kind, name=name,
        source_url=url, snapshot_id="sha256:" + hashlib.sha256(html.encode("utf-8")).hexdigest(),
        retrieved_at=retrieved_at or datetime.now(timezone.utc).isoformat(),
        attributes={k: v for k, v in attributes.items() if v}, scopes=tuple(scopes),
    ))


class Client:
    """Small cached fetches; two workers share a rate limit. No multimedia."""
    def __init__(self, cache, interval=0.5):
        self.cache = Path(cache)
        self.interval, self.lock, self.last = interval, threading.Lock(), 0.0
        self.timestamps = {}

    def fetch(self, url, refresh=False):
        url = source_url(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        path = self.cache / (key + ".json")
        if not refresh and path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            cached = json.loads(path.read_text(encoding="utf-8"))
            self.timestamps[url] = cached.get("retrieved_at", datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
            return cached["html"]
        with self.lock:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
        request = Request(quote(url, safe=":/?=&%"), headers={"User-Agent": "RailScope/1.0 (reference metadata import)"})
        with urlopen(request, timeout=25) as response:
            source_url(response.url)
            raw = response.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError("资料页面过大")
        html = raw.decode("utf-8-sig")
        self.timestamps[url] = datetime.now(timezone.utc).isoformat()
        atomic_json(path, {"url": url, "html": html, "retrieved_at": self.timestamps[url]})
        return html


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class ReferenceStore:
    def __init__(self, profiles=()):
        self.profiles = list(profiles)
        self.index = {}
        for profile in profiles:
            self.index.setdefault((profile["kind"], name_key(profile["name"], profile["kind"] == "station")), []).append(profile)
            if profile["kind"] == "line":
                for scope in profile.get("scopes", []):
                    scoped_attributes=dict(scope['attributes'])
                    endpoints=scope_terminals([scope])
                    if endpoints:
                        scoped_attributes.update(start_terminal=endpoints[0],end_terminal=endpoints[1])
                    scope_profile = {**profile, "attributes": scoped_attributes, "scopes": [scope]}
                    self.index.setdefault(("line", name_key(scope["name"])), []).append(scope_profile)
                    for alias in scope.get("aliases", []):
                        self.index.setdefault(("line", name_key(alias)), []).append(profile)

    def match(self, kind, names, record=None):
        hits = []
        for name in names:
            hits.extend(self.index.get((kind, name_key(name, kind == "station")), []))
        # Some index entries lead to equivalent pages through two query names.
        # Equivalent facts may share a reference; different scopes stay ambiguous.
        unique = {(name_key(p["name"]), json.dumps([p["attributes"], p.get("scopes", [])],
                                                  sort_keys=True, ensure_ascii=False)): p for p in hits}
        if len(unique) != 1:
            return None
        profile = next(iter(unique.values()))
        if kind == "station":
            record = record or {}
            locality = profile["attributes"].get("locality", "")
            city = str(record.get("city", "")).removesuffix("市")
            province = str(record.get("province", "")).removesuffix("省").removesuffix("市")
            folder = record.get("folder_path", [])
            local_cities = [city, *(str(v).removesuffix("市") for v in folder[1:2])]
            if province in {"北京", "上海", "天津", "重庆"}:
                local_cities.append(province)
            local = any(v and v != "城市待核对" and v in locality for v in local_cities)
            lines = {name_key(v) for v in record.get("line_names", [])}
            linked = {name_key(v) for v in profile["attributes"].get("main_lines", "").split("、")}
            if not local and not lines.intersection(linked):
                return None
        return profile


@lru_cache(maxsize=4)
def _load(path, stamp):
    if stamp is None:
        return ReferenceStore()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA or not isinstance(payload.get("profiles"), list):
        raise ValueError("网站参考资料文件格式无效")
    # Reparse explicit spans from older local snapshots as well as the shipped
    # reference. A local snapshot must not hide newly supported source fields.
    for profile in payload['profiles']:
        if profile['kind']=='line':
            endpoints=scope_terminals(profile.get('scopes',()))
            if endpoints:
                profile['attributes'].setdefault('start_terminal',endpoints[0])
                profile['attributes'].setdefault('end_terminal',endpoints[1])
    return ReferenceStore(payload["profiles"])


@lru_cache(maxsize=4)
def _combined(paths, stamps):
    profiles = {p['id']:p for p in _load(paths[0],stamps[0]).profiles}
    for p in _load(paths[1],stamps[1]).profiles:
        shared = profiles.get(p['id'])
        # The same original HTML can have richer parsed relationships after
        # upgrading the shipped parser, without inventing a newer snapshot.
        if shared and all(shared.get(k)==p.get(k) for k in ('snapshot_id','name','attributes')):
            continue
        profiles[p['id']] = p
    return ReferenceStore(list(profiles.values()))


def load_store(path=None):
    if path is None:
        paths = tuple(map(str,(SHARED_REFERENCE_PATH,REFERENCE_PATH)))
        stamps = tuple((p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else None
                       for p in (SHARED_REFERENCE_PATH,REFERENCE_PATH))
        return _combined(paths,stamps)
    path = Path(path)
    stamp = (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
    return _load(str(path), stamp)


def fill_missing(values, profile, fields):
    """Keep existing facts; report discrepancies per field with source evidence."""
    result, conflicts, provenance = dict(values), {}, {}
    for key, value in profile["attributes"].items():
        if key not in fields:
            continue
        if not result.get(key):
            result[key] = value
            provenance[key] = {k: profile[k] for k in ("source", "source_url", "snapshot_id", "retrieved_at", "verification_status", "confidence")}
        elif comparable_value(key, result[key]) != comparable_value(key, value):
            conflicts[key] = {"existing": result[key], "reference": value, "source_url": profile["source_url"]}
    return result, conflicts, provenance


def comparable_value(key, value):
    """Equivalent explicit units are not a discrepancy (1435 vs 1435 mm)."""
    text_value = str(value).strip()
    suffixes = {"_mm": "mm", "_kmh": "km/h", "_km": "km", "_m": "m", "_kw": "kw", "_t": "t"}
    unit = next((unit for suffix, unit in suffixes.items() if key.endswith(suffix)), None)
    if unit:
        numeric = re.sub(r"\s*" + re.escape(unit) + r"$", "", text_value, flags=re.I).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", numeric):
            return Decimal(numeric)
    return text_value


def without_previous_reference(properties):
    """Discard derived display facts before matching a refreshed object again."""
    props = dict(properties)
    previous = props.pop("external_reference", None)
    provenance = props.pop("reference_provenance", {})
    props.pop("reference_conflicts", None)
    if isinstance(previous, dict):
        for key in provenance:
            if props.get(key) == previous.get("attributes", {}).get(key):
                props.pop(key, None)
        if previous.get("kind") == "station":
            props.pop("station_overview", None)
    return props


def line_reference(record, store=None):
    # Station facilities must not inherit the attributes of the adjacent main line.
    if record.get("facility_only") or record.get("station_name"):
        return None
    names = [record.get(k, "") for k in ("line_name", "line_display_name", "name", "source_name")]
    return (store or load_store()).match("line", [n for n in names if n])


def station_reference(properties, record, custom=None, store=None):
    effective = {**(record or {}), **(custom or {})}
    names = [effective.get("name", ""), effective.get("display_name", ""), (properties or {}).get("name", "")]
    return (store or load_store()).match("station", [n for n in names if n], effective)


def import_references(destination=REFERENCE_PATH, station_names=None, progress=lambda message: None):
    from concurrent.futures import ThreadPoolExecutor
    destination = Path(destination)
    client = Client(destination.parent / "cache")
    profiles, errors, station_urls = [], [], set()
    index_paths = ["RailRoads/Mainlines/", "RailRoads/Area/", "RailRoads/Hub/", "RailRoads/Intercity/", "Trains/CR/", "Trains/Intercity/", "Trains/ALL/"]
    urls = set()
    for index in index_paths:
        progress("读取网站目录：" + index)
        try:
            soup = BeautifulSoup(client.fetch(BASE + index), "html.parser")
            urls.update(page_links(soup, "/RailRoads/Line/"))
            urls.update(page_links(soup, "/Trains/Model/Detail-"))
        except (OSError, ValueError) as error:
            errors.append({"url": BASE + index, "error": str(error)})

    def fetch_profile(url):
        try:
            html = client.fetch(url)
            p = parse_profile(html, url, client.timestamps.get(url))
            links = page_links(BeautifulSoup(html, "html.parser"), "/RailRoads/Station/") if p["kind"] == "line" else []
            return p, links, None
        except (OSError, ValueError) as error:
            return None, [], {"url": url, "error": str(error)}

    def batch(values):
        iterator = iter(sorted(values))
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(fetch_profile, url) for url in [next(iterator, None), next(iterator, None)] if url]
            i = 0
            while pending:
                profile, links, error = pending.pop(0).result()
                i += 1
                progress(f"读取资料 {i}/{len(values)}：" + (profile["name"] if profile else error["url"]))
                if error:
                    errors.append(error)
                else:
                    profiles.append(profile)
                    station_urls.update(links)
                url = next(iterator, None)
                if url:
                    pending.append(pool.submit(fetch_profile, url))

    batch(urls)
    if station_names is not None:
        wanted = {name_key(n, True) for n in station_names}
        station_urls = {u for u in station_urls if name_key(parse_qs(urlsplit(u).query).get("Station", [""])[0], True) in wanted}
    batch(station_urls)
    if not profiles:
        raise ValueError("未取得有效资料；已有参考文件保留")
    # Refreshes preserve successful old pages when a later request fails.
    old = (load_store() if destination == REFERENCE_PATH and not destination.exists() else load_store(destination)).profiles
    merged = {p["id"]: p for p in old}
    merged.update({p["id"]: p for p in profiles})
    result = {"schema": SCHEMA, "retrieved_at": datetime.now(timezone.utc).isoformat(),
              "source_url": BASE, "license_url": BASE + "About/Agreement/",
              "profiles": list(merged.values()), "errors": errors,
              "counts": {kind: sum(p["kind"] == kind for p in merged.values()) for kind in ("line", "station", "vehicle")}}
    progress("资料已读取，正在保存独立参考层…")
    atomic_json(destination, result)
    return result
