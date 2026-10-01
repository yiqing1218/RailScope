"""Compare one source railway with its imported physical graph (read-only).

python scripts/audit_rail_line.py --directory data/processed/rail/datasets/ID \
  --pbf data/raw/osm/china-latest.osm.pbf --name 杭昌高速线 --output report.json
"""
import argparse
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def audit(directory, pbf, name):
    import osmium
    directory, pbf = Path(directory), Path(pbf)
    source = {}
    for obj in (osmium.FileProcessor(str(pbf))
                .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
                .with_filter(osmium.filter.KeyFilter('railway'))):
        if obj.tags.get('name') == name:
            nodes = [n.ref for n in obj.nodes]
            source[str(obj.id)] = {tuple(sorted(pair)) for pair in zip(nodes, nodes[1:])}
    imported = defaultdict(set)
    line_counts = defaultdict(int)
    adjacency = defaultdict(set)
    coordinates = {}
    count = 0
    with closing(sqlite3.connect(directory/'rail_lines.sqlite')) as index, closing(sqlite3.connect(directory/'rail.sqlite')) as raw:
        index.execute('CREATE TEMP TABLE wanted(id TEXT PRIMARY KEY)')
        index.executemany('INSERT INTO wanted VALUES(?)', ((key,) for key in source))
        for edge_id, way, line in index.execute('SELECT e.id,e.source_way,e.line_id FROM edges e JOIN wanted w ON w.id=e.source_way'):
            edge = json.loads(raw.execute('SELECT data FROM edges WHERE id=?', (edge_id,)).fetchone()[0])
            count += 1
            line_counts[line] += 1
            nodes = edge.get('node_ids', (edge['from_node'],edge['to_node']))
            for node, coordinate in zip(nodes, edge['coordinates']):
                coordinates[node] = coordinate
            for a,b in zip(nodes,nodes[1:]):
                imported[way].add(tuple(sorted((a,b))))
                adjacency[a].add(b); adjacency[b].add(a)
    seen, components = set(), []
    for origin in adjacency:
        if origin in seen: continue
        pending, group = [origin], {origin}
        seen.add(origin)
        while pending:
            for node in adjacency[pending.pop()]:
                if node not in seen:
                    seen.add(node); group.add(node); pending.append(node)
        points = [coordinates[n] for n in group]
        components.append({'nodes':len(group), 'bounds':[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)],
                           'terminal_nodes':[n for n in group if len(adjacency[n])==1]})
    missing = {way:len(pairs-imported[way]) for way,pairs in source.items() if pairs-imported[way]}
    return {'line_name':name, 'source':str(pbf), 'source_size':pbf.stat().st_size,
            'source_version':str(pbf.stat().st_mtime_ns), 'directory':str(directory),
            'source_ways':len(source), 'imported_ways':sum(bool(imported[key]) for key in source),
            'imported_edges':count, 'line_memberships':dict(line_counts), 'missing_node_pairs_by_way':missing,
            'components':sorted(components,key=lambda c:-c['nodes']),
            'verification_status':'source_node_pairs_compared',
            'interpretation':'源连通分量不等于漏导；必须区分上下行、分支和源数据的单线表达。未生成补画几何。'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('directory','pbf','name','output'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args()
    result=audit(args.directory,args.pbf,args.name)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:result[key] for key in ('source_ways','imported_ways','imported_edges','missing_node_pairs_by_way')},ensure_ascii=False))
