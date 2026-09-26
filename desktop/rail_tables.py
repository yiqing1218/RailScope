"""Strict UTF-8 CSV train/stop tables referencing existing shared routes."""

import csv
import io

try:
    from .operating import parse_time, format_time
    from .rail import shared_document
    from .rail_station_import import station_choices, resolve_stops
except ImportError:
    from operating import parse_time, format_time
    from rail import shared_document
    from rail_station_import import station_choices, resolve_stops

LEGACY_COLUMNS = (
    "train_id",
    "route_id",
    "sequence",
    "node_id",
    "arrival",
    "departure",
    "platform_id",
    "from_track",
    "to_track",
    "via_node",
    "change_time",
)
COLUMNS = LEGACY_COLUMNS[:3] + ("stop_name",) + LEGACY_COLUMNS[3:]


def merge_csv(text, payload, choices=None):
    result = shared_document(payload)
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    aliases = {'车次':'train_id', '车次号':'train_id', '站名':'stop_name', '车站':'stop_name',
               '停站名称':'stop_name', '到达时间':'arrival', '到达':'arrival', '发车时间':'departure',
               '出发时间':'departure', '出发':'departure', '通道名称':'route_name', '通道编号':'route_id', '站序':'sequence',
               '车站名称':'stop_name', '到站时间':'arrival', '离站时间':'departure', '发车':'departure'}
    fields = [aliases.get(field.strip(), field.strip()) for field in (reader.fieldnames or [])]
    if (len(fields) != len(set(fields)) or not {'train_id','arrival','departure'} <= set(fields)
            or not {'node_id','stop_name'}.intersection(fields) or set(fields) - set(COLUMNS) - {'route_name'}):
        raise ValueError('CSV 至少需要 train_id,stop_name,arrival,departure；可选 route_name、route_id、sequence、node_id 及旧模板字段')
    reader.fieldnames = fields
    routes = {r["id"]: r for r in result["routes"]}
    choices = station_choices(result) if choices is None else choices
    existing = {t["id"] for t in result["trains"]}
    pending = {}
    for line, row in enumerate(reader, 2):
        if None in row or any(v is None for v in row.values()):
            raise ValueError(f"第 {line} 行列数不符")
        row = {**dict.fromkeys((*COLUMNS, 'route_name'), ''), **{k: v.strip() for k, v in row.items()}}
        ident = row["train_id"]
        if not ident or ident in existing:
            raise ValueError(f"第 {line} 行车次为空或重复：{ident}")
        rows = pending.setdefault(ident, [])
        if row['sequence'] and row['sequence'] != str(len(rows) + 1):
            raise ValueError(f'第 {line} 行站序不连续（从 1 开始）')
        if not row['node_id'] and not row['stop_name']:
            raise ValueError(f'第 {line} 行请填写停站名称')
        rows.append((line, row))
    if not pending:
        raise ValueError("CSV 没有车次记录")
    for ident, rows in pending.items():
        if len(rows) < 2:
            raise ValueError(f'{ident} 至少需要两个停站')
        ids = {row['route_id'] for _, row in rows if row['route_id']}
        names = {row['route_name'] for _, row in rows if row['route_name']}
        if len(ids) > 1 or len(names) > 1:
            raise ValueError(f'{ident} 通道不一致')
        candidates = [route for route in routes.values() if (not ids or route['id'] in ids)
                      and (not names or route.get('name', route['id']) in names)]
        if not candidates:
            raise ValueError(f'{ident} 共享通道不存在，请先建立完整通道')
        matches, failures = [], []
        for route in candidates:
            try:
                resolved = resolve_stops(rows, choices.get(route['id'], []))
                matches.append((route, resolved))
            except ValueError as error:
                failures.append(str(error))
        if not matches:
            raise ValueError(f'{ident} 未匹配通道：' + '；'.join(dict.fromkeys(failures))[:1200])
        if len(matches) > 1:
            raise ValueError(f'{ident} 匹配多条通道，请填写 route_name（通道名称）：' + '、'.join(r.get('name', r['id']) for r, _ in matches))
        route, stops = matches[0]
        for (line, row), stop in zip(rows, stops):
            arrival, departure = row['arrival'], row['departure']
            arrival = '' if arrival in ('始发','--','-') else arrival
            departure = '' if departure in ('终到','--','-') else departure
            stop.update(arrival_s=parse_time(arrival or departure), departure_s=parse_time(departure or arrival),
                        track_change={**{k: row[k] for k in ('from_track','to_track','via_node')}, 'time': row['change_time']})
            if row['platform_id']:
                if not row['platform_id'].isdigit():
                    raise ValueError(f'第 {line} 行站台编号无效')
                stop['platform_id'] = int(row['platform_id'])
        result['trains'].append({'id': ident, 'route_id': route['id'], 'stops': stops,
            'extensions': {'railscope.org/provenance': {'source': '用户导入的车次 CSV；非官方计划',
                'method': 'station_name_match' if any(not row['node_id'] for _, row in rows) else 'explicit_node',
                'version': 1, 'verification_status': 'user_timetable_not_dispatch_verified',
                'confidence': None,
                'matched_stops': [row['stop_name'] for _, row in rows],
                'snapshot': route.get('extensions', {}).get('railscope.org/line-resolution', {}).get('snapshot', 'portable_reference')}}})
    return result  # Caller compiles the whole batch before replacing its active model.


def export_csv(payload):
    payload = shared_document(payload)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    routes = {r["id"]: r for r in payload["routes"]}
    assembly_names = {
        int(item["node_id"]): item.get("name", "")
        for item in payload.get("extensions", {}).get("railscope.org/assembly", {}).get("stations", [])
        if isinstance(item, dict) and str(item.get("node_id", "")).isdigit()
    }
    named_choices = station_choices(payload)
    for train in payload["trains"]:
        if train.get("station_paths"):
            raise ValueError("含站场径路的车次请导出 JSON；CSV 不能无损保存嵌套路径")
        for sequence, stop in enumerate(train["stops"], 1):
            if stop.get("platform_ref"):
                raise ValueError(
                    "含统一站台引用 platform_ref 的车次请导出 JSON；旧 CSV 无法无损保存"
                )
            change = stop.get("track_change", {})
            shared = next(
                (
                    c
                    for c in routes[train["route_id"]].get("track_changes", [])
                    if c["node_id"] == stop["node_id"]
                ),
                {},
            )
            writer.writerow(
                {
                    "train_id": train["id"],
                    "route_id": train["route_id"],
                    "sequence": sequence,
                    "stop_name": next((c['name'] for c in named_choices.get(train['route_id'], [])
                                       if c['node_id'] == stop['node_id']), assembly_names.get(stop['node_id'], '')),
                    "node_id": stop["node_id"],
                    "arrival": format_time(stop["arrival_s"]),
                    "departure": format_time(stop["departure_s"]),
                    "platform_id": stop.get("platform_id", ""),
                    **{
                        k: change.get(k, shared.get(k, ""))
                        if change.get(k, shared.get(k)) is not None
                        else ""
                        for k in ("from_track", "to_track", "via_node")
                    },
                    "change_time": change.get("time", ""),
                }
            )
    return output.getvalue()


def export_template(payload, choices=None):
    """Create an importable example while keeping station names human readable."""
    payload = shared_document(payload)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=('train_id','route_name','sequence','stop_name','arrival','departure'))
    writer.writeheader()
    choices = station_choices(payload) if choices is None else choices
    if not payload['routes']:
        return output.getvalue()
    train = payload['trains'][0] if payload['trains'] else None
    route = next(r for r in payload['routes'] if not train or r['id'] == train['route_id'])
    candidates = choices.get(route['id'], [])
    names = {c['node_id']: c['name'] for c in candidates}
    stops = train['stops'] if train else ([{'node_id': c['node_id'], 'arrival_s': (8+i)*3600, 'departure_s': (8+i)*3600}
                                         for i, c in enumerate((candidates[0], candidates[-1]))] if len(candidates) >= 2 else [])
    if not stops or any(stop['node_id'] not in names for stop in stops):
        raise ValueError('当前通道缺少可匹配的站名，请先在软件中完善车站信息')
    for sequence, stop in enumerate(stops, 1):
        writer.writerow({'train_id':'示例车次_请修改', 'route_name':route.get('name',route['id']), 'sequence':sequence,
                         'stop_name': names[stop['node_id']], 'arrival':format_time(stop['arrival_s']),
                         'departure':format_time(stop['departure_s'])})
    return output.getvalue()
