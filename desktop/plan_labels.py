"""Station label dependency projection, never a second operations model.

References point into the existing DTOs. Infrastructure IDs, physical paths,
times and station routes are untouched by presentation edits.
"""

from collections import defaultdict
from dataclasses import replace


POSITION_KEY = 'railscope.org/station-track-positions'
STOP_KEY = 'railscope.org/track-position'
NAME_KEY = 'railscope.org/stop-name'


def owner(key):
    value = str(key or '').removeprefix('station:')
    return 'station:' + value if value.startswith(('node/', 'way/', 'relation/', 'signalbox/')) else None


class PlanLabels:
    def __init__(self, payload, graph, plan, shared_features=None):
        self.references = defaultdict(list)
        self.routes = defaultdict(list)
        self.stops = {}
        self.profiles = defaultdict(list)
        self._known_sources = {}
        self._directory = None
        self._repository = None
        self._station_points = defaultdict(list)
        shared_features = shared_features or {}
        for route in payload.get('routes', []):
            for position in route.get('extensions', {}).get(POSITION_KEY, []):
                key = owner(position.get('station_id'))
                if key:
                    self.references[key].append((position, 'station_name'))
                    self.routes[key].append(route)
        for point in graph.get('points', []):
            props = point['properties']
            key = owner(props.get('station_source_id') or
                        'node/' + str(props.get('source_station_node', props.get('osm_node_id'))))
            if key:
                self.references[key].append((props, 'name'))
        for train in payload.get('trains', []):
            profile = plan.lines.get('rail/' + train['id'], {})
            if profile:
                self.profiles[profile.get('corridor_id')].append(profile)
            for index, (stop, station) in enumerate(zip(train['stops'], profile.get('stations', []))):
                extensions = stop.get('extensions', {})
                position = extensions.get(STOP_KEY, {})
                matched = extensions.get(NAME_KEY, {})
                props = shared_features.get(stop['node_id'], {}).get('properties', {})
                key = owner(position.get('station_id') or matched.get('station_key') or
                            'node/' + str(props.get('source_station_node', props.get('osm_node_id'))))
                if key:
                    self.stops[train['id'], index] = key
                    self.references[key].append((station, 'name'))
                    if position:
                        self.references[key].append((position, 'station_name'))
                    if matched:
                        self.references[key].append((matched, 'display_name'))

    def refresh(self, library, changed=None, repository=None, bindings=None):
        if repository is not self._repository:
            self._repository = repository
            self._station_points.clear()
            if repository is not None:
                for point in repository.operational_points.values():
                    if point.point_type == 'station' and point.station_id:
                        self._station_points[point.station_id].append(point.id)
        if self._directory is not library.station_directory:
            self._directory = library.station_directory
            self._known_sources.clear()
        keys = self.references.keys() if changed is None else self.references.keys() & changed
        names, replacements, routes = {}, {}, {}
        self.changed_titles = {}
        for key in keys:
            source = key.removeprefix('station:')
            if source not in self._known_sources:
                self._known_sources[source] = source in library.station_directory
            if not self._known_sources[source]:
                continue
            name = library.endpoint_label(key)
            actual = False
            for target, field in self.references[key]:
                old = target.get(field, '')
                if old != name:
                    target[field] = name
                    actual = True
                    for route in self.routes[key]:
                        replacements.setdefault(id(route), {})[old] = name
                        routes[id(route)] = route
            if repository is not None:
                ident = (bindings or {}).get('station_sources', {}).get(source)
                station = repository.stations.get(ident)
                if station and station.name != name:
                    for point_id in self._station_points.get(ident, ()):
                        point = repository.operational_points[point_id]
                        # Preserve independently named operational boundaries.
                        if point.name == station.name:
                            repository.operational_points[point_id] = replace(point, name=name)
                    repository.stations[ident] = replace(station, name=name)
                    actual = True
            if actual:
                names[key] = name
        for ident, route in routes.items():
            title = route.get('name', '')
            if ' → ' in title and title.endswith((' · 单向通道', ' · 单向参考通道')):
                parts = title.split(' → ')
                parts[0] = replacements[ident].get(parts[0], parts[0])
                end, separator, suffix = parts[-1].partition(' · ')
                parts[-1] = replacements[ident].get(end, end) + separator + suffix
                route['name'] = ' → '.join(parts)
                if title != route['name']:
                    for profile in self.profiles.get(route['id'], ()):
                        if profile.get('name') == title:
                            profile['name'] = route['name']
                    self.changed_titles[route['id']] = route['name']
        return names
