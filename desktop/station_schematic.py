"""Vector station diagram from shared physical edges, never basemap pixels."""
from html import escape
import math


def ensure_export_font():
    """Qt's offscreen export can start without a system font collection."""
    import os
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    if 'Microsoft YaHei' not in QFontDatabase.families():
        font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))


def station_svg(repo, context=(), width=2400):
    station = next(iter(repo.stations.values()))
    tracks = list(repo.station_tracks.values())
    yard_ids = {ref.edge_id for track in tracks for ref in track.edge_refs}
    points = [p for key in yard_ids for p in repo.edges[key].coordinates]
    cx, cy = (sum(p[i] for p in points) / len(points) for i in (0, 1))
    cosine = math.cos(math.radians(cy))
    points = [((x-cx)*cosine, y-cy) for x,y in points]
    xx=sum(x*x for x,y in points); yy=sum(y*y for x,y in points); xy=sum(x*y for x,y in points)
    angle=.5*math.atan2(2*xy, xx-yy)
    def local(point):
        x,y=(point[0]-cx)*cosine,point[1]-cy
        return x*math.cos(angle)+y*math.sin(angle), -x*math.sin(angle)+y*math.cos(angle)
    rotated=[local(p) for key in yard_ids for p in repo.edges[key].coordinates]
    west,east=min(p[0] for p in rotated),max(p[0] for p in rotated)
    south,north=min(p[1] for p in rotated),max(p[1] for p in rotated)
    height=max(900,min(2400,220+len(tracks)*10))
    def project(point):
        x,y=local(point)
        return 140+(x-west)/max(east-west,1e-9)*(width-280), 150+(north-y)/max(north-south,1e-9)*(height-300)
    def path(coords, close=False):
        return 'M'+' L'.join(f'{x:.2f},{y:.2f}' for x,y in map(project,coords))+(' Z' if close else '')
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
         '<style>text{font-family:Microsoft YaHei;fill:#213c49}.track-label{font-size:14px;stroke:none}</style>',
         '<rect width="100%" height="100%" fill="#ffffff"/>',
         f'<text x="65" y="58" font-size="30" font-weight="700">RailScope · {escape(station.name)}站场示意图</text>',
         '<text x="65" y="89" font-size="15">依据当前轨道拓扑生成 · 自动参考，非联锁进路图 · 横纵比例分别调整以展示配线</text>',
         f'<defs><clipPath id="yard"><rect x="45" y="115" width="{width-90}" height="{height-215}"/></clipPath></defs><g clip-path="url(#yard)">']
    # Platform polygons/lines are real source geometry. No inferred buffers.
    for feature in context:
        props=feature.get('properties',{}); geometry=feature['geometry'];kind=geometry['type']
        if props.get('network_edge_id') or props.get('kind') in ('switch','station','halt'):
            continue
        if kind not in ('Polygon','MultiPolygon','LineString'):
            continue
        tag=props.get('way_tags',props)
        if tag.get('railway')!='platform' and props.get('kind')!='platform' and 'platform' not in str(props.get('asset_kind','')):
            continue
        polygons=geometry['coordinates'] if kind=='MultiPolygon' else [geometry['coordinates']] if kind=='Polygon' else None
        if polygons:
            for polygon in polygons:
                d=' '.join(path(ring,True) for ring in polygon)
                out.append(f'<path d="{d}" fill="#dae9e6" fill-rule="evenodd" stroke="#7ba69c" stroke-width="1.2"/>')
        else:
            out.append(f'<path d="{path(geometry["coordinates"])}" fill="none" stroke="#7ba69c" stroke-width="4"/>')
        platform_ref = tag.get('ref') or tag.get('name') or props.get('source_name')
        if platform_ref:
            ring = polygons[0][0] if polygons else geometry['coordinates']
            center = [sum(p[i] for p in ring)/len(ring) for i in (0,1)]
            x,y = project(center)
            out.append(f'<text x="{x:.1f}" y="{y+4:.1f}" text-anchor="middle" font-size="12">站台 {escape(str(platform_ref))}</text>')
    roles={ref.edge_id:track.role for track in tracks for ref in track.edge_refs}
    node_degree={}
    for key,edge in repo.edges.items():
        for node in (edge.from_node_id,edge.to_node_id):node_degree[node]=node_degree.get(node,0)+1
        main=roles.get(key)=='main' or (key not in yard_ids and edge.service not in ('yard','siding'))
        color='#1f6687' if main else '#526877'
        dash=' stroke-dasharray="9 5"' if edge.construction_status!='operating' else ''
        out.append(f'<path data-edge-id="{escape(key)}" d="{path(edge.coordinates)}" fill="none" stroke="{color}" stroke-width="{3.2 if main else 1.8}"{dash}/>')
        if key not in yard_ids:
            # Label at the actual endpoint attached to the yard.
            attached=next((n for n in (edge.from_node_id,edge.to_node_id) if any(n in (repo.edges[e].from_node_id,repo.edges[e].to_node_id) for e in yard_ids)),None)
            if attached:
                node=repo.nodes[attached];x,y=project((node.lon,node.lat))
                label=repo.lines.get(edge.infrastructure_line_id)
                if label and not label.name.startswith('未命名'):
                    out.append(f'<text x="{x:.1f}" y="{y-12:.1f}" class="track-label">接入 {escape(label.name)}</text>')
    for node,degree in node_degree.items():
        if degree>=3:
            value=repo.nodes[node];x,y=project((value.lon,value.lat))
            out.append(f'<circle data-node-id="{escape(node)}" cx="{x:.2f}" cy="{y:.2f}" r="3.8" fill="#c18334" stroke="#ffffff" stroke-width="1"/>')
    occupied=[]
    for track in sorted(tracks,key=lambda t:(-t.length_m,t.id)):
        # Half physical length, not the midpoint of a randomly split OSM way.
        ref=next((r for r in track.edge_refs if r.end_distance_m>=track.length_m/2),track.edge_refs[-1])
        coords=repo.edges[ref.edge_id].coordinates
        x,y=project(coords[len(coords)//2]);label=track.name.split(' · ')[-1]
        label_width=max(32,len(label)*14)
        label_y=y-7
        for _ in range(15):
            if not any(abs(label_y-old_y)<17 and abs(x-old_x)<(label_width+old_w)/2 for old_x,old_y,old_w in occupied):break
            label_y-=18
        occupied.append((x,label_y,label_width))
        if abs(label_y-y)>25:out.append(f'<path d="M{x:.1f},{y:.1f} L{x:.1f},{label_y+3:.1f}" stroke="#95a6aa" fill="none" stroke-width=".8"/>')
        out.append(f'<rect x="{x-label_width/2:.1f}" y="{label_y-14:.1f}" width="{label_width}" height="18" fill="white" fill-opacity=".90"/>')
        out.append(f'<text data-track-id="{escape(track.id)}" x="{x:.2f}" y="{label_y:.2f}" text-anchor="middle" class="track-label">{escape(label)}</text>')
    out.extend(['</g>',f'<line x1="65" y1="{height-70}" x2="105" y2="{height-70}" stroke="#1f6687" stroke-width="3.2"/>',
        f'<text x="115" y="{height-65}" font-size="15">正线 / 接入线</text>',
        f'<line x1="280" y1="{height-70}" x2="320" y2="{height-70}" stroke="#526877" stroke-width="1.8"/>',
        f'<text x="330" y="{height-65}" font-size="15">到发线 / 站场股道</text>',
        f'<circle cx="550" cy="{height-70}" r="4" fill="#c18334"/><text x="565" y="{height-65}" font-size="15">真实拓扑岔接点</text>',
        f'<text x="65" y="{height-30}" font-size="13">{len(tracks)} 根已识别股道 · 数据 © OpenStreetMap contributors · 暂编编号可人工修改 · 未关联站台不推测补画</text>', '</svg>'])
    return '\n'.join(out)
