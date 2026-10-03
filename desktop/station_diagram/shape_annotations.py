"""Place labels beside actual track groups and beyond their convergence."""
from collections import defaultdict
from statistics import median


def shape_annotations(repo, drawings, ownership, core, options):
    yards, lines = defaultdict(list), defaultdict(list)
    yard_edges = {}
    for track in repo.station_tracks.values():
        for ref in track.edge_refs:
            if ref.edge_id in drawings and track.yard_id in repo.yards:
                cx = (core[0]+core[2])/2
                points = drawings[ref.edge_id].points
                for a,b in zip(points,points[1:]):
                    if min(a[0],b[0]) <= cx <= max(a[0],b[0]) and abs(b[0]-a[0])>1e-6:
                        yards[track.yard_id].append((cx,a[1]+(cx-a[0])/(b[0]-a[0])*(b[1]-a[1])))
                yard_edges[track.yard_id] = ref.edge_id
    yard_labels = []
    for ident, points in sorted(yards.items()):
        yard_labels.append({'yard_id': ident, 'text': repo.yards[ident].name,
                            'edge_id': yard_edges[ident],
                            'point': (core[2]+30, median(p[1] for p in points))})
    for key, drawing in drawings.items():
        own = ownership[key]
        if drawing.role == 'main' and own.line_id and own.system and repo.lines[own.line_id].line_role!='connecting_line':
            for part in drawing.parts:
                for p in part:
                    if p[0] < core[0]-35 or p[0] > core[2]+35:
                        side = 'left' if p[0] < core[0] else 'right'
                        lines[own.line_id, side].append((p, key))
    line_labels = []
    for (ident, side), candidates in sorted(lines.items()):
        p, key = min(candidates, key=lambda v: abs(v[0][0]-(core[0] if side=='left' else core[2])))
        line_labels.append({'line_id': ident, 'edge_id': key, 'text': ownership[key].system,
                            'font_size': options.label_size*.7, 'convergence_node': '',
                            'point': (p[0], p[1]-16)})
    return yard_labels, line_labels
