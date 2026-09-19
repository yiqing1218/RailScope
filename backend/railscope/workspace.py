"""Canonical workspace repository and transactional, undoable editing service.

Only selected objects are materialized. National source geometry stays in the
viewport store. Source rows and manual overrides are separate SQLite tables.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, fields, replace
import json
from pathlib import Path
import sqlite3

from . import domain as d
from .repository import RailRepository
from .integrity import validate_repository, references, delete_edge, path_refs
from .identity import new_id

TYPES={
    'sources':d.DataSource,'snapshots':d.DatasetSnapshot,'lines':d.InfrastructureLine,
    'nodes':d.NetworkNode,'edges':d.NetworkEdge,'stations':d.Station,
    'sections':d.RouteSection,'corridors':d.Corridor,'station_routes':d.StationRoute,
    'train_services':d.TrainService,'train_runs':d.TrainRun,
    'station_tracks':d.StationTrack,'station_areas':d.StationArea,'platforms':d.Platform,
    'stop_positions':d.StopPosition,'entrances':d.Entrance,'blocks':d.BlockSection,
    'scenarios':d.DispatchScenario,
}
LIST_TYPES={'memberships':d.LineMembership,'stops':d.StopTime,'block_edges':d.BlockEdge,
            'headway_rules':d.HeadwayRule,'events':d.DispatchEvent,'occupancies':d.TrackOccupancy,'conflicts':d.Conflict}


def decode(cls, raw):
    raw=dict(raw)
    if cls is d.TrainRun and 'route_path_id' in raw:
        raw.setdefault('corridor_id',raw.pop('route_path_id'))
    for key in ('osm_node_ids','source_node_ids','source_member_ids'):
        if key in raw: raw[key]=tuple(raw[key])
    if 'coordinates' in raw: raw['coordinates']=tuple(tuple(p) for p in raw['coordinates'])
    if 'edge_refs' in raw: raw['edge_refs']=tuple(d.DirectedEdgeRef(**r) for r in raw['edge_refs'])
    return cls(**raw)


def _rows(repo):
    result={}
    for collection in TYPES:
        for key,obj in getattr(repo,collection).items():
            result[(collection,key)]=json.dumps(asdict(obj),ensure_ascii=False,sort_keys=True)
    for collection in LIST_TYPES:
        result[(collection,'@list')]=json.dumps([asdict(v) for v in getattr(repo,collection)],ensure_ascii=False,sort_keys=True)
    return result


class SQLiteWorkspace:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS workspace_source(kind TEXT,id TEXT,data TEXT NOT NULL,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS workspace_override(kind TEXT,id TEXT,data TEXT,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS workspace_meta(key TEXT PRIMARY KEY,value INTEGER);
                INSERT OR IGNORE INTO workspace_meta VALUES('revision',0);
                CREATE TABLE IF NOT EXISTS workspace_conflict(kind TEXT,id TEXT,reason TEXT,PRIMARY KEY(kind,id));
            ''')

    def seed(self,repo):
        """Import source rows without erasing manually saved object corrections."""
        validate_repository(repo)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for (kind,key),raw in _rows(repo).items():
                previous=db.execute('SELECT data FROM workspace_source WHERE kind=? AND id=?',(kind,key)).fetchone()
                # Historical infrastructure stays at its original snapshot until explicit migration.
                if previous and previous[0]!=raw and kind in ('edges','corridors','nodes'):
                    db.execute('INSERT OR REPLACE INTO workspace_conflict VALUES(?,?,?)',(kind,key,'source_changed: 保留旧快照，需检查兼容性'))
                    continue
                db.execute('INSERT OR REPLACE INTO workspace_source VALUES(?,?,?)',(kind,key,raw))
            db.execute("UPDATE workspace_meta SET value=value+1 WHERE key='revision'")

    def load(self):
        repo=RailRepository()
        with closing(sqlite3.connect(self.path)) as db:
            revision=db.execute("SELECT value FROM workspace_meta WHERE key='revision'").fetchone()[0]
            rows={(kind,key):raw for kind,key,raw in db.execute('SELECT kind,id,data FROM workspace_source')}
            rows.update({(kind,key):raw for kind,key,raw in db.execute('SELECT kind,id,data FROM workspace_override')})
        legacy_routes=[]
        for (kind,key),raw in rows.items():
            if raw is None: continue
            data=json.loads(raw)
            if kind in TYPES: getattr(repo,kind)[key]=decode(TYPES[kind],data)
            elif kind in LIST_TYPES: setattr(repo,kind,[decode(LIST_TYPES[kind],v) for v in data])
            elif kind=='routes': legacy_routes.append((key,data))
        for key,data in legacy_routes:
            if key in repo.corridors:
                continue
            refs=tuple(d.DirectedEdgeRef(**value) for value in data['edge_refs'])
            first,last=repo.edges[refs[0].edge_id],repo.edges[refs[-1].edge_id]
            repo.corridors[key]=d.Corridor(
                key,data.get('name',key),refs,
                first.from_node_id if refs[0].forward else first.to_node_id,
                last.to_node_id if refs[-1].forward else last.from_node_id,
                source_id='legacy_route_path',verification_status='unverified')
        validate_repository(repo)
        return repo,revision

    def save(self,repo,expected_revision):
        validate_repository(repo)
        rows=_rows(repo)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            revision=db.execute("SELECT value FROM workspace_meta WHERE key='revision'").fetchone()[0]
            if revision!=expected_revision: raise ValueError('工作区已被其他窗口修改，请重新载入')
            source={(kind,key):raw for kind,key,raw in db.execute('SELECT kind,id,data FROM workspace_source')}
            db.execute('DELETE FROM workspace_override')
            for kind,key in source.keys()|rows.keys():
                if source.get((kind,key))!=rows.get((kind,key)):
                    db.execute('INSERT INTO workspace_override VALUES(?,?,?)',(kind,key,rows.get((kind,key))))
            db.execute("UPDATE workspace_meta SET value=value+1 WHERE key='revision'")
        return revision+1

    def conflicts(self):
        with closing(sqlite3.connect(self.path)) as db:
            return [{'kind':k,'id':i,'reason':r} for k,i,r in db.execute('SELECT kind,id,reason FROM workspace_conflict')]


class EditSession:
    def __init__(self,store):
        self.store=store
        self.repo,self.revision=store.load()
        self.undo_stack,self.redo_stack=[],[]
        self.dirty=False

    def change(self,operation):
        candidate=deepcopy(self.repo)
        result=operation(candidate)
        validate_repository(candidate)
        self.undo_stack.append(self.repo)
        self.repo=candidate
        self.redo_stack.clear()
        self.dirty=True
        return result

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.repo);self.repo=self.undo_stack.pop();self.dirty=True

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.repo);self.repo=self.redo_stack.pop();self.dirty=True

    def save(self):
        self.revision=self.store.save(self.repo,self.revision)
        self.dirty=False

    def edit_line(self,line_id,**values):
        allowed={'name','railway_type','construction_status'}
        if not set(values)<=allowed: raise ValueError('不允许改变线路身份或原始来源')
        if 'name' in values and not values['name'].strip(): raise ValueError('线路名称不能为空')
        if 'construction_status' in values and values['construction_status'] not in {'operating','construction','planned','disused','unknown'}:
            raise ValueError('线路运营状态无效')
        return self.change(lambda r:r.lines.__setitem__(line_id,replace(r.lines[line_id],**values,verification_status='manual_override')))

    def assign_edges(self,line_id,edge_ids,remove=False):
        def apply(repo):
            if line_id not in repo.lines or any(e not in repo.edges for e in edge_ids): raise ValueError('线路或轨道不存在')
            repo.memberships[:]=[m for m in repo.memberships if not(m.line_id==line_id and m.edge_id in edge_ids)]
            if not remove: repo.memberships.extend(d.LineMembership(e,line_id,verification_status='manual_override') for e in dict.fromkeys(edge_ids))
        self.change(apply)

    def merge_lines(self,target,source):
        def apply(repo):
            if target==source or target not in repo.lines or source not in repo.lines: raise ValueError('请选择两条不同线路')
            repo.memberships[:]=list(dict.fromkeys(replace(m,line_id=target,verification_status='manual_override') if m.line_id==source else m for m in repo.memberships))
            for key,e in repo.edges.items():
                if e.infrastructure_line_id==source: repo.edges[key]=replace(e,infrastructure_line_id=target)
            del repo.lines[source]
        self.change(apply)

    def split_line(self,line_id,node_id,new_name,edge_ids):
        """Move an explicitly selected connected side, never infer an arbitrary fork."""
        def apply(repo):
            available={m.edge_id for m in repo.memberships if m.line_id==line_id}
            chosen=set(edge_ids)
            if not chosen or not chosen<available or node_id not in repo.nodes: raise ValueError('拆分须选择原线路部分轨道和真实端点')
            reached={node_id};pending=set(chosen)
            while pending:
                batch={e for e in pending if {repo.edges[e].from_node_id,repo.edges[e].to_node_id}&reached}
                if not batch: raise ValueError('拆分轨道须与指定端点连续')
                for e in batch: reached.update((repo.edges[e].from_node_id,repo.edges[e].to_node_id))
                pending-=batch
            if not any(node_id in (repo.edges[e].from_node_id,repo.edges[e].to_node_id) for e in available-chosen): raise ValueError('拆分节点须连接保留部分')
            new=new_id('IL')
            repo.lines[new]=replace(repo.lines[line_id],id=new,name=new_name,verification_status='manual_override')
            repo.memberships[:]=[replace(m,line_id=new,verification_status='manual_override') if m.line_id==line_id and m.edge_id in chosen else m for m in repo.memberships]
            return new
        return self.change(apply)

    def merge_stations(self,target,source):
        def apply(repo):
            if source==target: raise ValueError('请选择不同车站')
            a,b=repo.stations[target],repo.stations[source]
            repo.stations[target]=replace(a,source_member_ids=tuple(dict.fromkeys(a.source_member_ids+b.source_member_ids)),verification_status='manual_override')
            for key,node in repo.nodes.items():
                if node.station_id==source: repo.nodes[key]=replace(node,station_id=target)
            for collection in ('station_tracks','station_areas','platforms','station_routes','stop_positions','entrances'):
                objects=getattr(repo,collection)
                for key,obj in objects.items():
                    if obj.station_id==source: objects[key]=replace(obj,station_id=target)
            repo.stops[:]=[replace(s,station_id=target) if s.station_id==source else s for s in repo.stops]
            for key,obj in repo.train_runs.items():
                repo.train_runs[key]=replace(obj,origin_station_id=target if obj.origin_station_id==source else obj.origin_station_id,destination_station_id=target if obj.destination_station_id==source else obj.destination_station_id)
            del repo.stations[source]
        self.change(apply)

    def split_station(self,station_id,members,anchor_node_id,name):
        def apply(repo):
            old=repo.stations[station_id]
            if not members or not set(members)<set(old.source_member_ids): raise ValueError('拆出须选择部分原始成员')
            node=repo.nodes[anchor_node_id]
            new=new_id('ST')
            repo.stations[new]=replace(old,id=new,name=name,anchor_node_id=node.id,lon=node.lon,lat=node.lat,source_member_ids=tuple(members),verification_status='manual_override')
            repo.stations[station_id]=replace(old,source_member_ids=tuple(m for m in old.source_member_ids if m not in members),verification_status='manual_override')
            return new
        return self.change(apply)

    def edit_corridor(self,corridor_id,legs,name=None):
        def apply(repo):
            old=repo.corridors[corridor_id]
            refs=path_refs(repo,legs)
            first,last=repo.edges[refs[0].edge_id],repo.edges[refs[-1].edge_id]
            repo.corridors[corridor_id]=replace(old,name=name or old.name,edge_refs=refs,
                origin_node_id=first.from_node_id if refs[0].forward else first.to_node_id,
                destination_node_id=last.to_node_id if refs[-1].forward else last.from_node_id,
                verification_status='manual_override')
        self.change(apply)

    def delete_edge(self,edge_id):
        self.change(lambda repo:delete_edge(repo,edge_id))

    def references(self,kind,ident):
        return references(self.repo,kind,ident)
