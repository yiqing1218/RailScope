"""Reproduce five real-data before/after exports without editing the dataset."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'backend'), str(ROOT/'desktop')]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from desktop.station_schematic import ensure_export_font, station_svg, map_station_svg
from desktop.station_diagram_layout import DiagramOptions, build_layout
from desktop.station_diagram_render import write_diagram
from desktop.station_tracks import load_station_tracks, schematic_station_info
from railscope.workspace import decode, TYPES, LIST_TYPES
from railscope.repository import RailRepository


def fit(renderer, painter, rect):
    size = renderer.defaultSize()
    scale = min(rect.width()/size.width(), rect.height()/size.height())
    width, height = size.width()*scale, size.height()*scale
    renderer.render(painter, QRectF(rect.center().x()-width/2, rect.center().y()-height/2, width, height))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/station_diagram_examples')
    parser.add_argument('--identity', type=Path, default=ROOT/'data/processed/operations/workspace.sqlite')
    parser.add_argument('--cache', type=Path, default=ROOT/'data/logs/station_diagram_benchmark')
    args = parser.parse_args()
    directory = args.dataset
    if directory is None:
        root = ROOT/'data/processed/rail'
        directory = root/json.loads((root/'active_dataset.json').read_text(encoding='utf-8'))['dataset']
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    ensure_export_font()
    overrides = json.loads((ROOT/'data/catalog/rail_catalog_overrides.json').read_text(encoding='utf-8'))
    manifest = {'dataset': str(directory.resolve()), 'source': 'local OSM-derived shared infrastructure',
                'verification_status': 'automatic_reference', 'examples': []}
    options = DiagramOptions()
    args.cache.mkdir(parents=True, exist_ok=True)
    fingerprint = {'directory': str(directory.resolve()), 'source_mtime': (directory/'rail.sqlite').stat().st_mtime_ns,
                   'index_mtime': (directory/'rail_lines.sqlite').stat().st_mtime_ns,
                   'override_mtime': (ROOT/'data/catalog/rail_catalog_overrides.json').stat().st_mtime_ns,
                   'depth': options.topology_depth, 'loader_version': 2}
    with tempfile.TemporaryDirectory(prefix='railscope-diagram-') as temp:
        identity = Path(temp)/'identity.sqlite'
        if args.identity.exists():
            # SQLite backup includes any WAL state and never mutates user IDs.
            with closing(sqlite3.connect(args.identity.resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(identity)) as dst:
                src.backup(dst)
        with closing(sqlite3.connect((directory/'rail_lines.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
            stations = [(name, db.execute('SELECT source_id FROM station_directory WHERE name=?', (name+'站',)).fetchone())
                        for name in ('济南西', '郑州东', '商丘', '南京南', '重庆东')]
        for index, (name, row) in enumerate(stations, 1):
            if not row:
                raise ValueError('本地数据缺少车站：'+name)
            cache_path = args.cache/(name+'.json')
            cached = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
            if cached.get('fingerprint') == fingerprint:
                repo = RailRepository()
                for key,cls in TYPES.items():
                    setattr(repo,key,{k:decode(cls,v) for k,v in cached['repo'][key].items()})
                for key,cls in LIST_TYPES.items():
                    setattr(repo,key,[decode(cls,v) for v in cached['repo'][key]])
                context,info = cached['context'],cached['info']
            else:
                repo, rows, context = load_station_tracks(directory, identity,
                    {'name': name, 'station_source_id': row[0]}, overrides, approach_depth=options.topology_depth)
                info = schematic_station_info(directory, repo, rows, overrides)
                cache_path.write_text(json.dumps({'fingerprint': fingerprint,'repo':asdict(repo),'context':context,'info':info},ensure_ascii=False),encoding='utf-8')
            def source_digest():
                return hashlib.sha256(json.dumps([asdict(repo),context],sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
            original_digest = source_digest()
            before = map_station_svg(repo, context, station_info=info)
            after = station_svg(repo, context, station_info=info, options=options)
            stem = f'{index:02d}-{name}'
            (args.output/(stem+'-before.svg')).write_text(before, encoding='utf-8')
            write_diagram(args.output/(stem+'-after.svg'), after, options)
            write_diagram(args.output/(stem+'-after.png'), after, options)
            write_diagram(args.output/(stem+'-after.pdf'), after, options)
            comparison = QImage(1800, 900, QImage.Format.Format_ARGB32)
            comparison.fill(QColor('white'))
            painter = QPainter(comparison)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setFont(QFont('Microsoft YaHei', 18))
            painter.drawText(35, 40, name+'站：旧版原始几何输出')
            painter.drawText(915, 40, '新版分区压缩站场示意图')
            painter.setPen(QColor('#dce2e8'))
            painter.drawLine(890, 60, 890, 875)
            fit(QSvgRenderer(before.encode('utf-8')), painter, QRectF(20, 65, 850, 810))
            fit(QSvgRenderer(after.encode('utf-8')), painter, QRectF(910, 65, 865, 810))
            painter.end()
            comparison.save(str(args.output/(stem+'-comparison.png')))
            layout = build_layout(repo, context, options)
            assert all(d.points[0] == layout.nodes[repo.edges[k].from_node_id]
                       and d.points[-1] == layout.nodes[repo.edges[k].to_node_id] for k,d in layout.edges.items())
            variants = {}
            from dataclasses import replace
            for label, config in [('common-bend-off',replace(options,remove_common_bend=False)),
                                  ('common-bend-on',replace(options,remove_common_bend=True)),
                                  ('outlet-extension-off',replace(options,align_main_outlets=False)),
                                  ('outside-12',replace(options,outside_compression=12)),
                                  ('construction',replace(options,include_construction=True))]:
                variant = build_layout(repo,context,config)
                variants[label] = {'drawn_edges': len(variant.edges), 'visible_source_interval':variant.visible_source_interval,
                                   'canvas': [variant.width,variant.height]}
                write_diagram(args.output/(stem+'-'+label+'.svg'),station_svg(repo,context,station_info=info,options=config),config)
            assert all(v['canvas'] == list(options.canvas_size) for v in variants.values())
            assert variants['outside-12']['visible_source_interval'][1]-variants['outside-12']['visible_source_interval'][0] > layout.visible_source_interval[1]-layout.visible_source_interval[0]
            assert original_digest == source_digest()
            manifest['examples'].append({'station_name': name, 'station_id': next(iter(repo.stations)),
                'station_source': row[0], 'loaded_edges': len(repo.edges), 'drawn_edges': len(layout.edges),
                'platforms': len(layout.platforms), 'axis_source': layout.axis_source,
                'warnings': layout.warnings, 'options': asdict(options), 'variants':variants,
                'visible_source_interval':layout.visible_source_interval,
                'checks': {'shared_junctions':True,'source_unchanged':True,'variant_canvas_fixed':True,
                           'outside_compression_expands_source_interval':True}})
            print(name, 'loaded', len(repo.edges), 'drawn', len(layout.edges), 'platforms', len(layout.platforms), flush=True)
    (args.output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
