"""Diagram contracts: graph selection, shared junctions and effective controls."""
from copy import deepcopy
from dataclasses import replace
from xml.etree import ElementTree as ET

import pytest

from desktop.tests.test_station_outlets import pair_repository
from desktop.station_diagram_layout import DiagramOptions, build_layout
from desktop.station_schematic import station_svg


def test_connected_selection_ignores_nearby_geometry_and_obeys_depth():
    repo, context = pair_repository()
    edge = repo.edges['NE-0-0']
    repo.edges['NE-isolated'] = replace(edge, id='NE-isolated', from_node_id='NN-x', to_node_id='NN-y')
    core = build_layout(repo, context, DiagramOptions(topology_depth=0))
    full = build_layout(repo, context, DiagramOptions(topology_depth=2))
    assert len(core.edges) == 6 and len(full.edges) == 12
    assert 'NE-isolated' not in full.edges


def test_rotation_controls_symbols_and_source_are_independent():
    repo, context = pair_repository()
    context[0]['geometry'] = {'type':'LineString','coordinates':[[120.002,30.00005],[120.0041,30.00005]]}
    original = deepcopy((repo.edges, context))
    a = build_layout(repo, context, DiagramOptions())
    b = build_layout(repo, context, DiagramOptions(station_compression=8, platform_width=2))
    assert b.platforms[0].width > a.platforms[0].width
    assert b.platforms[0].length < a.platforms[0].length
    assert b.core_bounds[2]-b.core_bounds[0] < a.core_bounds[2]-a.core_bounds[0]
    assert (repo.edges, context) == original


def test_content_style_and_metadata():
    repo, context = pair_repository()
    svg = ET.fromstring(station_svg(repo, context))
    assert int(svg.attrib['width']) > int(svg.attrib['height'])
    assert 'data-north-angle' in next(e.attrib for e in svg.iter() if 'data-north-angle' in e.attrib)
    assert svg.find('{http://www.w3.org/2000/svg}metadata') is not None
    text = station_svg(repo, context, options=DiagramOptions(show_platforms=False, show_legend=False,
                       show_north=False, show_title=False, show_endpoints=False))
    assert all(key not in text for key in ('data-platform-id', 'data-legend', 'data-north-angle', 'data-line-id', 'data-title'))


@pytest.mark.parametrize('field,value', [('station_compression', 0), ('dpi', 0), ('width', -1),
                                       ('topology_depth', -1), ('orientation', 'bad')])
def test_reject_invalid_options(field, value):
    with pytest.raises(ValueError):
        DiagramOptions(**{field: value})


def test_external_compactness_does_not_change_real_selection():
    repo,context = pair_repository()
    a = build_layout(repo,context,DiagramOptions(outside_compression=4))
    b = build_layout(repo,context,DiagramOptions(outside_compression=12))
    assert (a.width,a.height,a.geometry_scale) == (b.width,b.height,b.geometry_scale)
    assert b.edges.keys() == a.edges.keys()
    assert b.visible_source_interval[0] < a.visible_source_interval[0]
    assert b.visible_source_interval[1] > a.visible_source_interval[1]
    for point in repo.edges['NE-0-0'].coordinates:
        assert a.project(point)[1] == pytest.approx(b.project(point)[1])


@pytest.mark.parametrize('mapping', ['system','line'])
def test_station_tracks_require_explicit_line_membership_for_color(mapping):
    from railscope.domain import InfrastructureLine,NetworkEdge,StationTrack
    from railscope.integrity import path_refs
    repo,context = pair_repository()
    primary = repo.edges['NE-0-0-yard']
    a,b = primary.coordinates
    key = 'NE-arrival'
    repo.lines['IL-local'] = InfrastructureLine('IL-local','第5道','rail')
    repo.edges[key] = NetworkEdge(key,primary.from_node_id,primary.to_node_id,
        (a,((a[0]+b[0])/2,a[1]+.0001),b),200,track_role='arrival_departure_track',
        service='siding',infrastructure_line_id='IL-local')
    repo.station_tracks[key] = StationTrack(key,'ST-a','到发线',edge_refs=path_refs(repo,[(key,True)]))
    options = DiagramOptions(color_overrides={'IL-0':'#123456'}) if mapping=='system' else DiagramOptions(line_overrides={'IL-0':{'color':'#123456'}})
    svg = ET.fromstring(station_svg(repo,context,options=options))
    paths = {e.attrib['data-edge-id']:e for e in svg.iter() if 'data-edge-id' in e.attrib}
    assert paths[key].attrib['stroke'] == '#85919b'
    assert paths[primary.id].attrib['stroke'] == '#123456'
    assert paths[key].attrib['stroke-width'] == paths[primary.id].attrib['stroke-width']
    assert build_layout(repo,context,options).systems[key] is None
    repo.station_tracks[key] = replace(repo.station_tracks[key],infrastructure_line_id='IL-0')
    svg = ET.fromstring(station_svg(repo,context,options=options))
    paths = {e.attrib['data-edge-id']:e for e in svg.iter() if 'data-edge-id' in e.attrib}
    assert paths[key].attrib['stroke'] == '#123456'


def test_construction_is_optional_dashed_and_planned_is_excluded():
    repo,context = pair_repository()
    repo.edges['NE-0-0'] = replace(repo.edges['NE-0-0'],construction_status='construction')
    repo.edges['NE-1-0'] = replace(repo.edges['NE-1-0'],construction_status='planned')
    a = build_layout(repo,context)
    assert 'NE-0-0' not in a.edges and 'NE-1-0' not in a.edges
    options = DiagramOptions(include_construction=True)
    svg = ET.fromstring(station_svg(repo,context,options=options))
    paths = {e.attrib['data-edge-id']:e for e in svg.iter() if 'data-edge-id' in e.attrib}
    assert paths['NE-0-0'].attrib['stroke-dasharray'] == '10 7'
    assert 'NE-1-0' not in paths


def test_common_bend_removal_preserves_relative_offsets():
    from desktop.station_diagram_geometry import common_baseline,baseline_value
    base = [(0,0),(100,20),(200,40),(300,20),(400,0)]
    paths = [[(x,y+offset) for x,y in base] for offset in (-10,10)]
    reference = common_baseline(paths,(0,400))
    for x,y in base:
        assert baseline_value(reference,x) == pytest.approx(y)
        assert (y+10-baseline_value(reference,x))-(y-10-baseline_value(reference,x)) == 20


def test_bend_reference_joins_fragmented_parallel_pair_without_steps():
    from desktop.station_diagram_geometry import reference_paths,common_baseline,baseline_value
    from railscope.domain import NetworkNode,NetworkEdge
    from railscope.repository import RailRepository
    repo = RailRepository()
    raw = {}
    for track,y in enumerate((0,12)):
        for i,x in enumerate((0,95 if track==0 else 100,200)):
            node = f'NN-{track}-{i}'
            repo.nodes[node] = NetworkNode(node,x,y)
        for i in (0,1):
            key = f'NE-{track}-{i}'
            a,b = f'NN-{track}-{i}',f'NN-{track}-{i+1}'
            pa,pb = repo.nodes[a],repo.nodes[b]
            raw[key] = ((pa.lon,pa.lat),(pb.lon,pb.lat))
            repo.edges[key] = NetworkEdge(key,a,b,raw[key],100)
    paths = reference_paths(repo,sorted(raw),raw)
    assert len(paths)==2
    knots = common_baseline(paths,(0,200))
    assert all(baseline_value(knots,x)==pytest.approx(6) for x in (94,95,96,99,100,101))


def test_only_main_outlets_have_labels_and_no_switch_or_leader():
    repo,context = pair_repository()
    repo.lines['IL-1'] = replace(repo.lines['IL-1'],name='某联络线',line_role='connecting_line')
    svg = ET.fromstring(station_svg(repo,context))
    assert not any('data-node-id' in e.attrib for e in svg.iter())
    # Collision-avoiding leaders are allowed for the new platform/track labels.
    assert not any(e.tag.endswith('circle') for e in svg.iter())
    labels = [e for e in svg.iter() if 'data-line-id' in e.attrib]
    assert all(e.attrib['data-line-id'] != 'IL-1' for e in labels)
    assert all(e.attrib['data-end'] in ('left','right') for e in labels)
    assert not any(e.attrib.get('data-convergence-line')=='IL-1' for e in svg.iter())


def test_shared_junction_coordinates_survive_bend_and_compression():
    repo,context = pair_repository()
    layout = build_layout(repo,context,DiagramOptions(remove_common_bend=True))
    for key,drawing in layout.edges.items():
        edge = repo.edges[key]
        assert drawing.points[0] == layout.nodes[edge.from_node_id]
        assert drawing.points[-1] == layout.nodes[edge.to_node_id]


def test_dialog_preview_controls_and_png_pdf_output(qtbot,tmp_path):
    from PySide6.QtWidgets import QDialog
    from PySide6.QtGui import QImage
    from desktop.station_diagram_ui import StationDiagramDialog
    from desktop.station_diagram_render import write_diagram
    repo,context = pair_repository()
    settings = tmp_path/'settings.json'
    dialog = StationDiagramDialog(repo,context,settings_path=settings)
    qtbot.addWidget(dialog)
    assert dialog.preview.renderer().isValid()
    dialog.controls['station_compression'].setValue(6)
    dialog.controls['include_construction'].setChecked(True)
    assert dialog.refresh_preview()
    options = dialog.options()
    assert options.station_compression == 6 and options.include_construction
    dialog.save_settings(options)
    reopened = StationDiagramDialog(repo,context,settings_path=settings)
    qtbot.addWidget(reopened)
    assert reopened.options() == options
    svg = station_svg(repo,context,options=options)
    for suffix in ('png','pdf','svg'):
        path = tmp_path/('example.'+suffix)
        write_diagram(path,svg,options)
        assert path.stat().st_size > 100
    image = QImage(str(tmp_path/'example.png'))
    assert (image.width(),image.height()) == options.canvas_size
    assert image.dotsPerMeterX() == round(options.dpi/.0254)
    assert (tmp_path/'example.pdf').read_bytes().startswith(b'%PDF')
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_png_text_keeps_solid_glyphs_with_halo(qtbot, tmp_path):
    from PySide6.QtGui import QImage
    from desktop.station_diagram_render import write_diagram

    repo, context = pair_repository()
    options = DiagramOptions()
    svg = station_svg(repo, context, options=options)
    root = ET.fromstring(svg)
    title = next(e for e in root.iter() if e.attrib.get('data-title') == 'true')
    # A plain fill is the independent reference; Qt does not support SVG's
    # paint-order, so white strokes over the text would erase its dark ink.
    attrs = {key: title.attrib[key] for key in
             ('x', 'y', 'font-size', 'font-weight', 'text-anchor')}
    attrs.update(fill='#25364a', stroke='none', **{'font-family': 'Microsoft YaHei,Arial'})
    reference = ET.Element('text', attrs)
    reference.text = title.text
    reference_svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{root.attrib["width"]}" '
        f'height="{root.attrib["height"]}"><rect width="100%" height="100%" fill="white"/>'
        + ET.tostring(reference, encoding='unicode') + '</svg>'
    )
    write_diagram(tmp_path / 'actual.png', svg, options)
    write_diagram(tmp_path / 'reference.png', reference_svg, options)
    top = max(0, int(float(title.attrib['y']) - options.title_size * 1.5))
    bottom = int(float(title.attrib['y']) + 2)

    def dark_ink(path):
        image = QImage(str(path))
        return sum(image.pixelColor(x, y).lightness() < 110
                   for y in range(top, bottom) for x in range(image.width()))

    reference_ink = dark_ink(tmp_path / 'reference.png')
    assert reference_ink > 100
    assert dark_ink(tmp_path / 'actual.png') >= reference_ink * .97


def test_ports_are_direction_layout_anchors_without_changing_source():
    import json
    repo,context = pair_repository()
    for key,edge in list(repo.edges.items()):
        if not key.endswith('yard'):
            point=(120,edge.coordinates[0][1])
            repo.edges[key]=replace(edge,coordinates=(point,edge.coordinates[-1]))
            repo.nodes[edge.from_node_id]=replace(repo.nodes[edge.from_node_id],lon=point[0])
    original = deepcopy(repo)
    a = build_layout(repo,context,DiagramOptions(align_main_outlets=False))
    b = build_layout(repo,context)
    for side,index in (('left',0),):
        ports = [p for p in b.ports if p['side']==side]
        assert ports
        # Crop crossings lie on the frame; real graph leaves retain their
        # transformed position rather than being stretched to a new endpoint.
        assert all(p['point'][0] >= b.plot_bounds[index] for p in ports)
        assert all(p['point'][0] == b.plot_bounds[index] for p in ports)
    assert all(p['extended'] for p in b.ports)
    assert b.extensions and not a.extensions
    assert [p['original_points'] for p in b.ports] == [p['original_points'] for p in a.ports]
    assert repo == original
    svg = ET.fromstring(station_svg(repo,context))
    metadata = json.loads(svg.find('{http://www.w3.org/2000/svg}metadata').text)
    assert all(p['direction_source']=='length_uniform_linear_fit' for p in metadata['ports'])


def test_manual_port_text_visibility_and_extension_overrides():
    repo,context = pair_repository()
    ports = build_layout(repo,context).ports
    left = next(p for p in ports if p['line'].id=='IL-0' and p['side']=='left')
    hidden = next(p for p in ports if p['line'].id=='IL-1')
    options = DiagramOptions(port_overrides={left['key']:{'text':'自定正线\n往测试城市','dx':-8,'dy':12,'extend':False},
                                           hidden['key']:{'visible':False}})
    layout = build_layout(repo,context,options)
    assert not next(p for p in layout.ports if p['key']==left['key'])['extended']
    svg = ET.fromstring(station_svg(repo,context,options=options))
    labels = [e for e in svg.iter() if e.attrib.get('data-line-id')=='IL-0']
    assert len(labels)==1 and labels[0].attrib['data-end']=='left'
    assert ''.join(e.text or '' for e in labels[0] if e.tag.endswith('text'))=='自定正线往测试城市'
    assert not any(e.attrib.get('data-line-id')=='IL-1' for e in svg.iter())
    edited=next(p for p in layout.ports if p['key']==left['key'])
    assert float(labels[0].attrib['x'])==pytest.approx(edited['point'][0]-24,abs=.02)


def test_line_manual_color_role_and_hide_are_presentation_only():
    repo,context = pair_repository()
    original = deepcopy(repo)
    options = DiagramOptions(line_overrides={'IL-0':{'role':'station','color':'#112233','width':1.25},
                                            'IL-1':{'visible':False},'IL-2':{'visible':False}})
    svg = ET.fromstring(station_svg(repo,context,options=options))
    paths = [e for e in svg.iter() if 'data-edge-id' in e.attrib]
    assert paths and all(e.attrib['data-role']=='station' and e.attrib['stroke']=='#112233'
                         and e.attrib['stroke-width']=='1.25' for e in paths)
    assert not any('data-line-id' in e.attrib for e in svg.iter())
    assert repo==original


def test_smooth_baseline_rejects_short_median_step():
    from desktop.station_diagram_geometry import smooth_baseline,baseline_value
    reference = smooth_baseline(((0,0),(100,0),(100.025,3.38),(300,3.38),(600,4)))
    assert reference
    assert abs(baseline_value(reference,100.025)-baseline_value(reference,100)) < .01
    assert DiagramOptions().remove_common_bend


def test_semi_automatic_editors_roundtrip(qtbot,tmp_path):
    from desktop.station_diagram_ui import StationDiagramDialog
    repo,context = pair_repository()
    settings = tmp_path/'editor.json'
    dialog = StationDiagramDialog(repo,context,settings_path=settings)
    qtbot.addWidget(dialog)
    row = next(r for r in range(dialog.line_table.rowCount()) if dialog.line_table.item(r,0).data(256)=='IL-0')
    dialog.line_table.item(row,4).setText('#112233')
    dialog.line_table.item(row,5).setText('4.25')
    dialog.line_table.cellWidget(row,6).setCurrentIndex(2)
    portrow = next(r for r in range(dialog.port_table.rowCount()) if dialog.port_table.item(r,0).data(256)=='IL-1:left')
    dialog.port_table.item(portrow,2).setText('用户线路\\n往用户城市')
    assert dialog.port_table.cellWidget(portrow,3).currentData() is None
    assert dialog.refresh_preview()
    options = dialog.options()
    assert options.line_overrides['IL-0']['color']=='#112233'
    assert options.line_overrides['IL-0']['label'] is False
    assert options.port_overrides['IL-1:left']['text']=='用户线路\n往用户城市'
    assert 'extend' not in options.port_overrides['IL-1:left']
    dialog.save_settings(options)
    reopened = StationDiagramDialog(repo,context,settings_path=settings)
    qtbot.addWidget(reopened)
    assert reopened.options()==options
    dialog.reset_editor('port')
    assert not dialog.options().port_overrides
