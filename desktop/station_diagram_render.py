"""Limited system palette, large side-only annotations and vector/raster export."""
from dataclasses import asdict
from html import escape
from pathlib import Path
import hashlib
import json
import math
from functools import lru_cache

try:
    from .station_diagram_layout import DiagramOptions, build_layout, smooth_path, system_name
    from .station_schematic import station_projection, port_destination, ensure_export_font, mainline_label
except ImportError:
    from station_diagram_layout import DiagramOptions, build_layout, smooth_path, system_name
    from station_schematic import station_projection, port_destination, ensure_export_font, mainline_label


STYLE_TEMPLATES = {'professional': {'ink': '#25364a', 'platform': '#dce1e6', 'neutral': '#85919b'}}
SYSTEM_COLORS = {'京沪': '#2463a3', '济郑': '#b8453e', '商合杭': '#33846e',
                 '沪汉蓉': '#75609a', '沪蓉': '#75609a', '合福': '#a87731',
                 '徐兰': '#39844d', '郑渝': '#a87731', '京广': '#465992'}
SYSTEM_COLORS.update({'渝厦':'#b8453e','渝万':'#3293a5','重庆东环':'#465992',
                      '京港':'#b8453e','陇海':'#465992','宁安':'#a87731','郑机':'#2463a3','郑开':'#75609a'})
PALETTE = ('#2463a3', '#b8453e', '#33846e', '#75609a', '#a87731', '#465992')


def stable_color(name):
    known = next((color for key,color in SYSTEM_COLORS.items() if key in name),None)
    return known or PALETTE[int(hashlib.sha256(name.encode('utf-8')).hexdigest()[:8],16)%len(PALETTE)]


@lru_cache(maxsize=128)
def system_palette(names):
    result,used = {},set()
    ordered = sorted(names,key=lambda name:(not any(key in name for key in SYSTEM_COLORS),name))
    for name in ordered:
        preferred = stable_color(name)
        known = any(key in name for key in SYSTEM_COLORS)
        color = preferred if known or preferred not in used else next((c for c in PALETTE if c not in used),preferred)
        result[name] = color
        used.add(color)
    return result


def edge_color(repo,edge,options,role=None,system=None):
    line = repo.lines.get(edge.infrastructure_line_id)
    name = system or (system_name(line) if mainline_label(line) and role == 'main' else None)
    own = options.line_overrides.get(edge.infrastructure_line_id,{})
    if own.get('color'):
        return own['color']
    if line and line.id in options.color_overrides:
        return options.color_overrides[line.id]
    if name and name in options.color_overrides:
        return options.color_overrides[name]
    if system:
        inherited = next((options.line_overrides.get(line.id,{}).get('color') or options.color_overrides.get(line.id)
                          for line in sorted(repo.lines.values(),key=lambda line:line.id)
                          if system_name(line) == system and (options.line_overrides.get(line.id,{}).get('color') or line.id in options.color_overrides)),None)
        if inherited:
            return inherited
    if options.color_scheme == 'mono':
        return '#334c65' if role == 'main' else '#64788b'
    names = tuple(sorted({system_name(line) for line in repo.lines.values() if mainline_label(line)}))
    return system_palette(names).get(name,stable_color(name)) if name else '#85919b'


def text_lines(text,length):
    return [text[i:i+length] for i in range(0,len(text),length)] or ['']


def render_svg(repo,context=(),options=None,station_info=None,template='professional'):
    options = options or DiagramOptions()
    info = station_info or {}
    style = STYLE_TEMPLATES[template]
    layout = build_layout(repo,context,options)
    station = next(iter(repo.stations.values()))
    w,h = layout.width,layout.height
    left,top,right,bottom = layout.plot_bounds
    fs = options.label_size
    local,*_ = station_projection(repo)
    metadata = {'schema':'railscope.station-diagram.v2','station_id':station.id,
        'attribution':'数据 © OpenStreetMap contributors',
        'source':'shared_repository_topology','verification_status':'automatic_reference','confidence':None,
        'axis_source':layout.axis_source,'axis_angle_degrees':math.degrees(layout.angle),'options':asdict(options),
        'station_interval':layout.station_interval,'visible_source_interval':layout.visible_source_interval,
        'geometry_scale':layout.geometry_scale,'common_baseline':layout.baseline,'warnings':layout.warnings,
        'selected_edge_ids':sorted(layout.edges),
        'edges':{key:{'source_id':repo.edges[key].source_id,'snapshot':repo.edges[key].snapshot_id,
                     'verification_status':repo.edges[key].verification_status,'confidence':repo.edges[key].confidence,
                     'role':drawing.role,'external':drawing.external,'color_system':layout.systems.get(key),
                     'role_source':'manual_override' if options.line_overrides.get(repo.edges[key].infrastructure_line_id,{}).get('role','auto')!='auto' else 'domain_or_display_inference'}
                 for key,drawing in layout.edges.items()},
        'ports':[{key:p[key] for key in ('key','side','point','points','original_points','extended','visible')}
                 | {'line_id':p['line'].id,'edge_ids':sorted(p['edge_ids']),
                    'label_source':'manual_override' if 'text' in options.port_overrides.get(p['key'],{}) else 'automatic_reference',
                    'extension_source':'schematic_display_only' if p['extended'] else None} for p in layout.ports],
        'line_destinations':info.get('line_destinations',{})}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<metadata>'+escape(json.dumps(metadata,ensure_ascii=False))+'</metadata>',
        '<style>text{font-family:Microsoft YaHei,Arial;fill:'+style['ink']+'}</style>',
        '<rect width="100%" height="100%" fill="#ffffff"/>']
    if options.show_title:
        out.append(f'<text data-title="true" x="{w/2:.2f}" y="{options.margin+options.title_size:.2f}" text-anchor="middle" font-size="{options.title_size}" font-weight="700">{escape(station.name)}平面布置图</text>')
    out.append(f'<g data-track-bounds="{left},{top},{right},{bottom}">')
    if options.show_platforms:
        for p in layout.platforms:
            fill = {'gray':style['platform'],'tint':'#dcebe4','outline':'#ffffff'}[options.platform_fill]
            out.append(f'<rect data-platform-id="{escape(p.id)}" data-source-geometry="{p.source_kind}" data-symbol="diagram-only" x="{p.x-p.length/2:.2f}" y="{p.y-p.width/2:.2f}" width="{p.length:.2f}" height="{p.width:.2f}" transform="rotate({p.angle:.4f} {p.x:.2f} {p.y:.2f})" rx="2" fill="{fill}" stroke="#91a0ae" stroke-width="1"/>')
    ranked = {'auxiliary':0,'connector':1,'station':2,'main':3}
    for key,drawing in sorted(layout.edges.items(),key=lambda item:(ranked[item[1].role],item[0])):
        edge = repo.edges[key]
        rule = options.line_overrides.get(edge.infrastructure_line_id,{})
        color = rule.get('color') or edge_color(repo,edge,options,drawing.role,layout.systems.get(key))
        weight = rule.get('width') or {'main':options.main_width,'station':options.station_width,'connector':options.connector_width,'auxiliary':options.connector_width*.7}[drawing.role]
        dash = ' stroke-dasharray="10 7"' if edge.construction_status == 'construction' else ''
        d = ' '.join(smooth_path(part) for part in drawing.parts)
        out.append(f'<path data-edge-id="{escape(key)}" data-role="{drawing.role}" data-zone="{drawing.zone}" data-external="{str(drawing.external).lower()}" d="{d}" fill="none" stroke="{color}" stroke-width="{weight:.2f}" stroke-linecap="round" stroke-linejoin="round"{dash}/>')
    out.append('</g>')
    for port in layout.ports:
        if not port['extended']:
            continue
        key = sorted(port['edge_ids'])[0]
        edge = repo.edges[key]
        rule = options.line_overrides.get(edge.infrastructure_line_id,{})
        color = rule.get('color') or edge_color(repo,edge,options,port['role'],layout.systems.get(key))
        weight = rule.get('width') or {'main':options.main_width,'station':options.station_width,
                                     'connector':options.connector_width,'auxiliary':options.connector_width*.7}[port['role']]
        dash = ' stroke-dasharray="10 7"' if edge.construction_status == 'construction' else ''
        for original,extended in zip(port['original_points'],port['points']):
            if math.dist(original,extended) > .1:
                out.append(f'<line data-outlet-extension="{escape(port["key"])}" data-source-edge-id="{escape(key)}" '
                    f'x1="{original[0]:.2f}" y1="{original[1]:.2f}" x2="{extended[0]:.2f}" y2="{extended[1]:.2f}" '
                    f'stroke="{color}" stroke-width="{weight}" stroke-linecap="round"{dash}/>')
    if options.show_endpoints:
        for side in ('left','right'):
            ports = sorted((p for p in layout.ports if p['side']==side and p['visible']),key=lambda p:(p['point'][1],system_name(p['line'])))
            groups = []
            for port in ports:
                source_port = {**port,'side':'right' if port['vector'][0]>=0 else 'left'}
                destination = port_destination(source_port,info,local)
                available = left-options.margin-20 if side=='left' else w-right-options.margin-20
                chars = max(4,int(available/fs))
                rule = options.port_overrides.get(port['key'],{})
                if 'text' in rule:
                    lines = [line for chunk in rule['text'].splitlines() for line in text_lines(chunk,chars)]
                else:
                    lines = text_lines(system_name(port['line']),chars)
                    if destination:
                        lines += text_lines('往'+destination,chars)
                if not lines:
                    continue
                groups.append((port,lines))
            required = sum(len(lines)*fs*1.22+18 for _,lines in groups)
            if required > bottom-top and ports:
                raise ValueError('端点标签过密，请增大输出尺寸、降低横纵比或减少外围层数')
            # Labels sit directly alongside actual main-line ports. They are
            # never distributed down a gutter with leaders to a throat fan.
            positions = []
            for port,lines in groups:
                preferred = port['point'][1]-(len(lines)-1)*fs*.61
                positions.append(max(top+fs,min(bottom-len(lines)*fs*1.22,preferred)))
            for i in range(1,len(positions)):
                positions[i] = max(positions[i],positions[i-1]+len(groups[i-1][1])*fs*1.22+12)
            if positions and positions[-1]+len(groups[-1][1])*fs*1.22 > bottom:
                positions[-1] = bottom-len(groups[-1][1])*fs*1.22
                for i in range(len(positions)-2,-1,-1):
                    positions[i] = min(positions[i],positions[i+1]-len(groups[i][1])*fs*1.22-12)
            if positions and positions[0] < top:
                raise ValueError('正线端口标注过密，请增大画布或减小字号')
            for (port,lines),y in zip(groups,positions):
                key = sorted(port['edge_ids'])[0]
                color = options.line_overrides.get(repo.edges[key].infrastructure_line_id,{}).get('color') or edge_color(repo,repo.edges[key],options,port['role'],layout.systems.get(key))
                rule = options.port_overrides.get(port['key'],{})
                x = (port['point'][0]-18 if side=='left' else port['point'][0]+18)+float(rule.get('dx',0))
                y += float(rule.get('dy',0))
                anchor = 'end' if side=='left' else 'start'
                attrs = (f'data-line-id="{escape(port["line"].id)}" data-end="{side}" data-port-x="{port["point"][0]:.2f}" data-port-y="{port["point"][1]:.2f}" '
                         f'data-track-count="{len(port["points"])}" data-port-edges="{escape(" ".join(sorted(port["edge_ids"])))}"')
                out.append(f'<g {attrs} x="{x:.2f}" y="{y:.2f}" style="fill:{color}">')
                for i,text in enumerate(lines):
                    out.append(f'<text x="{x:.2f}" y="{y+i*fs*1.22:.2f}" text-anchor="{anchor}" font-size="{fs:.2f}" font-weight="{600 if i==0 else 400}" style="fill:{color}">{escape(text)}</text>')
                out.append('</g>')
    if options.show_north:
        degrees = math.degrees(layout.angle)
        nx,ny = w-options.margin-70,options.margin+78
        out.append(f'<g data-north-angle="{degrees:.4f}" transform="translate({nx:.2f} {ny:.2f}) rotate({degrees:.4f})"><line x1="0" y1="30" x2="0" y2="-25" stroke="#25364a" stroke-width="3"/><polygon points="0,-40 -9,-19 0,-24 9,-19" fill="#25364a"/></g>')
        tx,ty = nx+math.sin(layout.angle)*58,ny-math.cos(layout.angle)*58
        out.append(f'<text x="{tx:.2f}" y="{ty+fs*.3:.2f}" text-anchor="middle" font-size="{fs}">N</text>')
    if options.show_legend:
        legend = set()
        for key,drawing in layout.edges.items():
            edge = repo.edges[key]
            line = repo.lines.get(edge.infrastructure_line_id)
            name = layout.systems.get(key) or (system_name(line) if mainline_label(line) and drawing.role == 'main' else None)
            if name and not name.startswith('未命名'):
                legend.add((name,options.line_overrides.get(edge.infrastructure_line_id,{}).get('color') or edge_color(repo,edge,options,drawing.role,layout.systems.get(key))))
        out.append('<g data-legend="true">')
        entries = sorted(legend)
        if options.include_construction and any(repo.edges[k].construction_status=='construction' for k in layout.edges):
            entries.append(('在建铁路','#7c8791'))
        sizes = [len(name)*fs*.85+80 for name,_ in entries]
        total = sum(sizes)+20*max(0,len(entries)-1)
        x,y = max(options.margin,(w-total)/2),h-90
        for (name,color),size in zip(entries,sizes):
            if x+size > w-options.margin:
                x = options.margin
                y += fs*1.5
            if y > h-20:
                raise ValueError('图例内容过多，请增大输出尺寸')
            dash = ' stroke-dasharray="10 7"' if name=='在建铁路' else ''
            out.append(f'<line x1="{x:.2f}" y1="{y:.2f}" x2="{x+45:.2f}" y2="{y:.2f}" stroke="{color}" stroke-width="{options.main_width}"{dash}/>')
            out.append(f'<text x="{x+58:.2f}" y="{y+fs*.32:.2f}" font-size="{fs*.85:.2f}">{escape(name)}</text>')
            x += size+20
        out.append('</g>')
    out.append('</svg>')
    return '\n'.join(out)


def write_diagram(path,svg,options):
    """Exact PNG pixel size and physical DPI; vector SVG/PDF retain paths."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in ('.svg','.png','.pdf'):
        raise ValueError('仅支持 SVG / PNG / PDF')
    if suffix == '.svg':
        path.write_text(svg,encoding='utf-8')
        return
    from PySide6.QtCore import QRectF,QSizeF,QMarginsF
    from PySide6.QtGui import QImage,QPainter,QPdfWriter,QPageSize,QPageLayout
    from PySide6.QtSvg import QSvgRenderer
    ensure_export_font()
    renderer = QSvgRenderer(svg.encode('utf-8'))
    if not renderer.isValid():
        raise ValueError('示意图 SVG 无法渲染')
    size = renderer.defaultSize()
    if suffix == '.pdf':
        device = QPdfWriter(str(path))
        device.setResolution(options.dpi)
        device.setPageSize(QPageSize(QSizeF(size.width()*25.4/options.dpi,size.height()*25.4/options.dpi),QPageSize.Unit.Millimeter))
        device.setPageMargins(QMarginsF(0,0,0,0),QPageLayout.Unit.Millimeter)
    else:
        device = QImage(size,QImage.Format.Format_ARGB32)
        device.fill('white')
        ppm = round(options.dpi/.0254)
        device.setDotsPerMeterX(ppm)
        device.setDotsPerMeterY(ppm)
    painter = QPainter(device)
    if not painter.isActive():
        raise ValueError('无法打开导出绘图设备')
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter,QRectF(0,0,device.width(),device.height()))
    finally:
        painter.end()
    if suffix == '.png' and not device.save(str(path)):
        raise ValueError('示意图无法写入')
