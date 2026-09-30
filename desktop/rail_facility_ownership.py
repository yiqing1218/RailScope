"""Rebuildable presentation ownership from real OSM facility areas.

Source feature/edge keys are adapter references, never new operational IDs.
No source geometry, catalog group or shared domain entity is rewritten.
"""
from contextlib import closing
from collections import deque
import json
from pathlib import Path
import sqlite3

try:
    from .rail_semantics import semantic_record
    from .rail_station_types import FACILITY_TYPES
except ImportError:
    from rail_semantics import semantic_record
    from rail_station_types import FACILITY_TYPES


FACILITY_KINDS = frozenset(('yard','depot','workshop','works','engine_shed'))


def connected_access_tracks(db,index_path,owners,overrides,station_ids):
    """Follow explicitly named depot access tracks through real endpoints.

    Proximity cannot attach an access line. An access shared by different
    facilities stays unresolved, and a known main track is a stopping boundary.
    """
    if not Path(index_path).exists():
        return owners
    extra,conflicts = {},set()
    with closing(sqlite3.connect(Path(index_path).resolve().as_uri()+'?mode=ro',uri=True)) as index:
        if not index.execute("SELECT 1 FROM sqlite_master WHERE name='edges'").fetchone():
            return owners
        queue = deque()
        def enqueue(key,evidence):
            row=index.execute('SELECT a,b FROM edges WHERE id=?',(key,)).fetchone()
            if row:
                queue.extend((node,evidence) for node in row)
        for key,evidence in owners.items():
            if evidence['station_id']:
                enqueue(key,evidence)
        visited=set()
        while queue:
            node,evidence=queue.popleft()
            marker=(node,evidence['station_id'])
            if marker in visited:
                continue
            visited.add(marker)
            for key,a in index.execute('SELECT id,a FROM edges WHERE a=? UNION SELECT id,a FROM edges WHERE b=?',(node,node)):
                if key in owners:
                    continue
                row=db.execute('SELECT data FROM edges WHERE id=?',(key,)).fetchone()
                if not row:
                    continue
                edge=json.loads(row[0]);tags=edge.get('way_tags',{})
                name=tags.get('name') or tags.get('full_name') or ''
                if not any(word in name for word in ('动车','动走','车辆段','机务段','检修','出入段','客整','整备')):
                    continue
                if semantic_record(edge)['track_role']=='main_track':
                    continue
                x,y=edge['coordinates'][0 if a==node else -1]
                feature_row=next(((ident,raw) for ident,raw in db.execute(
                    "SELECT f.id,f.data FROM bounds b JOIN features f ON f.id=b.id WHERE f.kind='rail' "
                    'AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?',(x-1e-7,x+1e-7,y-1e-7,y+1e-7))
                    if json.loads(raw)['properties'].get('network_edge_id')==key),None)
                if not feature_row:
                    continue
                feature_id,raw=feature_row;track=json.loads(raw)['properties']
                object_key='object:network_edge_id:'+str(key)
                edit={**overrides.get(track.get('catalog_group_id'),{}),**overrides.get(object_key,{})}
                facts=semantic_record(track,edit)
                if facts['track_role']=='main_track':
                    continue
                explicit=next((edit.get(field) for field in ('station_id','station_source') if edit.get(field) in station_ids),
                              edit.get('station_id') or edit.get('station_source'))
                assigned=(None if edit.get('station_assignment')=='pending' else explicit if explicit in station_ids
                          else evidence['station_id'] if not explicit else None)
                record={**evidence,'station_id':assigned,'feature_id':feature_id,'object_key':object_key,
                        'catalog_id':track.get('catalog_group_id'),
                        'source':'workspace_override' if explicit else 'connected_facility_access_track',
                        'properties':{field:track.get(field) for field in
                            ('line_name','display_name','track_type','track_role','from_name','to_name')}}
                if key in extra and extra[key]['station_id']!=assigned:
                    conflicts.add(key)
                else:
                    extra[key]=record
                if assigned:
                    enqueue(key,record)
    return {**owners,**{key:value for key,value in extra.items() if key not in conflicts}}


def facility_track_owners(directory, stations, overrides):
    """Return unambiguous per-edge directory owners, with source evidence.

    Explicit assignments win. Automatic ownership requires almost the entire
    physical source edge to lie in a real source polygon; overlapping named
    facilities remain unresolved. Main tracks are always excluded.
    """
    source = Path(directory)/'rail.sqlite'
    station_ids = {record['id'] for record in stations}
    facilities = {record['id']:record for record in stations
                  if overrides.get('station:'+record['id'],{}).get('station_type',record.get('station_type')) in FACILITY_TYPES
                  or record.get('kind') in FACILITY_KINDS}
    if not facilities or not source.exists():
        return {}
    from shapely.geometry import shape
    owners = {}
    ambiguous = set()
    snapshot = str(source.stat().st_mtime_ns)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'features','bounds'} <= tables:
            return {}
        for (raw,) in db.execute("SELECT data FROM features WHERE kind='railStationAreas'"):
            area = json.loads(raw)
            props = area.get('properties',{})
            if props.get('boundary_kind') in ('platform','station_building'):
                continue
            members = {str(value) if str(value).startswith(('node/','way/','relation/')) else 'node/'+str(value)
                       for value in props.get('associated_station_ids',[])}
            members.add(props.get('infrastructure_id'))
            candidates = members & facilities.keys()
            if len(candidates) != 1:
                continue
            owner = next(iter(candidates))
            geometry = shape(area['geometry'])
            if geometry.geom_type not in ('Polygon','MultiPolygon') or not geometry.is_valid:
                continue
            west,south,east,north = geometry.bounds
            for feature_id,track_raw in db.execute(
                    "SELECT f.id,f.data FROM bounds b JOIN features f ON f.id=b.id WHERE f.kind='rail' "
                    'AND b.maxx>=? AND b.minx<=? AND b.maxy>=? AND b.miny<=?',(west,east,south,north)):
                feature = json.loads(track_raw)
                track = feature.get('properties',{})
                edge_id = track.get('network_edge_id')
                if not edge_id:
                    continue
                key = 'object:network_edge_id:'+str(edge_id)
                edit = {**overrides.get(track.get('catalog_group_id'),{}),**overrides.get(key,{})}
                facts = semantic_record(track,edit)
                if facts['track_role']=='main_track':
                    continue
                explicit = next((edit.get(field) for field in ('station_id','station_source') if edit.get(field) in station_ids),
                                edit.get('station_id') or edit.get('station_source'))
                assigned = (None if edit.get('station_assignment')=='pending' else
                            explicit if explicit in station_ids else owner if not explicit else None)
                line = shape(feature['geometry'])
                if line.geom_type!='LineString' or not line.length or line.intersection(geometry).length/line.length < .98:
                    continue
                evidence = {'station_id':assigned,'feature_id':feature_id,'object_key':key,
                    'catalog_id':track.get('catalog_group_id'),
                    'properties':{field:track.get(field) for field in
                        ('line_name','display_name','track_type','track_role','from_name','to_name')},
                    'source':'workspace_override' if explicit or edit.get('station_assignment')=='pending' else 'osm_facility_area_membership',
                    'source_member_ids':[props.get('infrastructure_id')],
                    'snapshot':snapshot,'version':1,'verification_status':'user_named' if explicit else 'automatic_reference','confidence':None}
                if edge_id in owners and owners[edge_id]['station_id'] != assigned:
                    ambiguous.add(edge_id)
                else:
                    owners[edge_id] = evidence
        owners={key:record for key,record in owners.items() if key not in ambiguous}
        return connected_access_tracks(db,Path(directory)/'rail_lines.sqlite',owners,overrides,station_ids)
