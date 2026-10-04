"""Small presentation indexes over effective overrides, without GIS copies."""

from functools import wraps
from threading import RLock


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call

try:
    from .rail_line_workspace import EffectiveOverrides, ASSEMBLY_PREFIX
except ImportError:
    from rail_line_workspace import EffectiveOverrides, ASSEMBLY_PREFIX


class QueryOverrides(EffectiveOverrides):
    def __init__(self, values):
        super().__init__(values)
        self._lock = RLock()
        self.revision = 0
        self._line_ids = None
        self._line_names = {}
        self._assembly_members = {}
        self.station_names = {}
        self.station_connections = {}
        self.station_keys = set()
        self.node_names = {}
        for key in self:
            value = self.get(key)
            if value.get('assembly_id'):
                self._assembly_members.setdefault(value['assembly_id'], set()).add(key)
            self._index_station(key, value)

    def _index_station(self, key, value):
        value = value if isinstance(value, dict) else {}
        if key.startswith('station:'):
            if key in self:
                self.station_keys.add(key)
            else:
                self.station_keys.discard(key)
            for index, field in ((self.station_names, 'display_name'),
                                 (self.station_connections, 'connected_lines')):
                if value.get(field) is not None:
                    index[key] = value[field]
                else:
                    index.pop(key, None)
        if key.startswith(('object:network_node_id:', 'object:osm_node_id:',
                           'object:infrastructure_id:node/', 'node:', 'switch:node/')):
            if value.get('display_name'):
                self.node_names[key] = value['display_name']
            else:
                self.node_names.pop(key, None)

    def _changed(self, key, old):
        self.revision += 1
        value = self.get(key, {})
        self._index_station(key, value)
        before, after = old.get('assembly_id'), value.get('assembly_id')
        if before:
            self._assembly_members.get(before, set()).discard(key)
        if after:
            self._assembly_members.setdefault(after, set()).add(key)
        affected = {key}
        if key.startswith(ASSEMBLY_PREFIX):
            affected.update(self._assembly_members.get(key.removeprefix(ASSEMBLY_PREFIX), ()))
            ident = key.removeprefix(ASSEMBLY_PREFIX)
            if self._line_ids is not None:
                self._line_ids.add(ident)
                affected.add(ident)
        for ident in affected:
            self._index_line(ident)

    def _index_line(self, key):
        if self._line_ids is None or key not in self._line_ids:
            return
        name = self.get(key, {}).get('display_name')
        marker = self.get(ASSEMBLY_PREFIX + key, {})
        if marker.get('active', True):
            name = marker.get('attributes', {}).get('display_name') or marker.get('name') or name
        if name:
            self._line_names[key] = str(name).casefold()
        else:
            self._line_names.pop(key, None)

    @synchronized
    def line_aliases(self, db, workspace, names, query):
        if self._line_ids is None:
            self._line_ids = {key for (key,) in db.execute('SELECT id FROM main.lines')}
            self._line_ids.update(workspace.groups)
            self._line_ids.update(workspace.retired)
            for key in self._line_ids:
                self._index_line(key)
        folded = query.casefold()
        result = [key for key, name in self._line_names.items() if folded in name]
        result.extend(key for key, name in names.items() if folded in str(name).casefold())
        return result

    @synchronized
    def __setitem__(self, key, value):
        old = self.get(key, {})
        super().__setitem__(key, value)
        self._changed(key, old)

    @synchronized
    def __delitem__(self, key):
        old = self.get(key, {})
        super().__delitem__(key)
        self._changed(key, old)

    def pop(self, key, *default):
        if key not in self:
            if default:
                return default[0]
            raise KeyError(key)
        value = self[key]
        del self[key]
        return value

    def update(self, values=(), **kwargs):
        for key, value in dict(values, **kwargs).items():
            self[key] = value

    def clear(self):
        for key in list(self):
            del self[key]

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def popitem(self):
        if not self:
            raise KeyError('empty overrides')
        key = next(reversed(self))
        return key, self.pop(key)

    @synchronized
    def station_snapshot(self):
        return (dict(self.station_names), dict(self.station_connections),
                sorted(self.station_keys), dict(self.node_names))
