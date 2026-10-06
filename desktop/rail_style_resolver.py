"""Pure domain-fact to display-style mapping; no names or compound type tests."""
CLASS_LABELS = {'high_speed': '高速铁路', 'conventional': '普速铁路', 'freight': '货运铁路',
                'other': '其他铁路', 'metro': '地铁', 'industrial': '工业铁路', 'unknown': '类别待核实'}
ROLE_LABELS = {'main_track': '正线', 'arrival_departure_track': '到发线',
    'shunting_track': '调车线', 'lead_track': '牵出线', 'freight_track': '货物线',
    'depot_track': '段管线', 'locomotive_running_track': '机车走行线',
    'maintenance_track': '检修线', 'stabling_track': '存车线', 'turnback_track': '折返线',
    'crossover': '渡线', 'spur_track': '支接用途轨道', 'safety_siding': '安全线',
    'escape_siding': '避难线', 'other_station_track': '其他站线', 'unknown': '用途待核实'}
LINE_ROLE_LABELS = {'main_line': '正线', 'branch_line': '支线', 'connecting_line': '联络线',
                    'dedicated_line': '专用线', 'industrial_line': '工业线', 'unknown': '网络角色待核实'}

_LEGACY_CLASSES = {'high_speed': ('高速铁路线', '高速铁路站场股道'),
    'conventional': ('普速铁路线', '普速铁路站场股道'), 'freight': ('货运铁路线', '货运站场股道'),
    'metro': ('未确认类型', '站场股道（类型待核对）'),
    'other': ('未确认类型', '站场股道（类型待核对）'),
    'industrial': ('货运铁路线', '货运站场股道'), 'unknown': ('未确认类型', '站场股道（类型待核对）')}
STYLE_SOURCES = {}
STYLE_LABELS = {}
for _class, (_main, _station) in _LEGACY_CLASSES.items():
    STYLE_SOURCES['class.' + _class] = _main
    STYLE_SOURCES['class.' + _class + '.station'] = _station
    STYLE_LABELS['class.' + _class] = CLASS_LABELS[_class] + ' · 轨道'
    STYLE_LABELS['class.' + _class + '.station'] = CLASS_LABELS[_class] + ' · 站线'
for _role, _label in ROLE_LABELS.items():
    if _role in ('main_track', 'arrival_departure_track', 'unknown'):
        continue
    STYLE_SOURCES['role.' + _role] = {
        'crossover': '渡线 / 道岔连接轨', 'spur_track': '支线 / 岔道',
        'turnback_track': '折返线', 'maintenance_track': '车辆段 / 检修线',
        'depot_track': '车辆段 / 检修线'}.get(_role, '站场股道（类型待核对）')
    STYLE_LABELS['role.' + _role] = _label
for _role, _old in (('connecting_line','联络线 / 匝道'), ('branch_line','支线 / 岔道')):
    STYLE_SOURCES['line.' + _role] = _old
    STYLE_LABELS['line.' + _role] = LINE_ROLE_LABELS[_role]
for _speed in (350,250,200,160):
    STYLE_SOURCES['class.high_speed.' + str(_speed)] = f'高速铁路线 · {_speed} km/h'
    STYLE_LABELS['class.high_speed.' + str(_speed)] = f'高速铁路 · {_speed} km/h'


def legacy_style_key(facts):
    role = facts.get('track_role', 'unknown')
    category = facts.get('railway_class', 'unknown')
    category = category if category in CLASS_LABELS else 'unknown'
    if 'role.' + role in STYLE_SOURCES:
        return 'role.' + role
    if role == 'arrival_departure_track' or (role != 'main_track' and facts.get('facility_only')):
        return 'class.' + category + '.station'
    if facts.get('line_role') in ('connecting_line', 'branch_line'):
        return 'line.' + facts['line_role']
    if category == 'high_speed':
        speed = facts.get('design_speed_kmh')
        if speed in (None, ''):
            speed = 350
        elif isinstance(speed, str) and speed.isdigit():
            speed = int(speed)
        if speed in (350,250,200,160):
            return 'class.high_speed.' + str(speed)
    return 'class.' + category


# One selector contract for the object editor, style editor and map adapter.
GROUP_LABELS = {'track': '轨道线', 'station': '车站线'}
CATEGORY_LABELS = {'conventional': '普速铁路', 'high_speed': '高速铁路',
                   'freight': '货运铁路', 'other': '其他铁路', 'industrial': '工业铁路',
                   'metro': '地铁', 'unknown': '类别待核实'}
TRACK_LINE_LABELS = {'main_line': '正线', 'branch_line': '支线', 'connecting_line': '联络线',
                     'dedicated_line': '专用线', 'industrial_line': '工业线', 'unknown': '线路功能待核实'}
STATION_LINE_LABELS = {'unknown': '站场轨道线', **{k: v for k, v in ROLE_LABELS.items()
                                               if k != 'main_track' and k != 'unknown'}}
STATION_LINE_LABELS['spur_track'] = '连接线'
SPEED_BANDS = {'300-350': '300–350 km/h', '250-300': '250–300 km/h',
               '200-250': '200–250 km/h', '150-200': '150–200 km/h',
               'unknown': '速度范围待核实'}
COMPAT_STYLE_SOURCES = dict(STYLE_SOURCES)
DEFAULT_STYLE_KEY = '_default'
CONFIGURED_STYLE_KEYS = '_configured_style_keys'
STATUS_LABELS = {'operating': '运营中', 'construction': '在建', 'planned': '规划',
                 'disused': '停用', 'unknown': '状态待核实'}
INACTIVE_STATUSES = frozenset(('construction', 'planned', 'disused'))
STATUS_ALIASES = {**{key: key for key in STATUS_LABELS},
                  **{label: key for key, label in STATUS_LABELS.items()},
                  '运营': 'operating', '已开通': 'operating', '停运': 'disused',
                  '废弃': 'disused', '规划中': 'planned', '建设中': 'construction'}


def classification_path(facts):
    """Exactly the selector vocabulary used by both editors and the map."""
    group, category, function, band = line_selection(facts)
    functions = TRACK_LINE_LABELS if group == 'track' else STATION_LINE_LABELS
    path = (GROUP_LABELS[group], CATEGORY_LABELS[category],
            functions.get(function, '线路功能待核实'))
    return path + ((SPEED_BANDS[band],) if band else ())


def speed_band(facts):
    technical = facts.get('technical_attributes') or {}
    band = facts.get('speed_band') or technical.get('speed_band')
    if band in SPEED_BANDS:
        return band
    speed = facts.get('design_speed_kmh') or technical.get('design_speed_kmh')
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return 'unknown'
    # Shared boundaries go into the upper band; 350 is included in the last band.
    for low, high in ((300, 350), (250, 300), (200, 250), (150, 200)):
        if low <= speed < high or low == 300 and speed == 350:
            return f'{low}-{high}'
    return 'unknown'


def line_selection(facts):
    role = facts.get('track_role', 'unknown')
    group = 'station' if role != 'main_track' and (
        facts.get('facility_only') or role not in ('main_track', 'unknown')) else 'track'
    category = facts.get('railway_class')
    category = category if category in CATEGORY_LABELS else 'other'
    function = role if group == 'station' and role in STATION_LINE_LABELS else (
        facts.get('line_role') if facts.get('line_role') in TRACK_LINE_LABELS else 'unknown')
    if group == 'station' and function not in STATION_LINE_LABELS:
        function = 'unknown'
    return group, category, function, speed_band(facts) if category == 'high_speed' else None


def selection_style_key(group, category, function, band=None):
    return '.'.join((group, category, function, band or 'unknown')) if category == 'high_speed' else '.'.join((group, category, function))


def style_key(facts):
    selection = line_selection(facts)
    return selection_style_key(*selection)


STYLE_SELECTIONS = {}
for _group, _functions in (('track', TRACK_LINE_LABELS), ('station', STATION_LINE_LABELS)):
    for _category, _class_label in CATEGORY_LABELS.items():
        for _function, _function_label in _functions.items():
            for _band in (SPEED_BANDS if _category == 'high_speed' else (None,)):
                _key = selection_style_key(_group, _category, _function, _band)
                _facts = {'railway_class': _category, 'line_role': _function if _group == 'track' else 'unknown',
                          'track_role': 'main_track' if _group == 'track' else _function,
                          'facility_only': _group == 'station', 'design_speed_kmh':
                          {'300-350': 350, '250-300': 250, '200-250': 200, '150-200': 160}.get(_band)}
                _old = legacy_style_key(_facts)
                STYLE_SOURCES[_key] = COMPAT_STYLE_SOURCES[_old]
                STYLE_LABELS[_key] = ' / '.join((GROUP_LABELS[_group], _class_label, _function_label)) + (
                    ' / ' + SPEED_BANDS[_band] if _band else '')
                STYLE_SELECTIONS[_key] = (_group, _category, _function, _band)


def semantic_summary(facts):
    return ' · '.join((CLASS_LABELS.get(facts.get('railway_class'), '类别待核实'),
                     ROLE_LABELS.get(facts.get('track_role'), '用途待核实')))
