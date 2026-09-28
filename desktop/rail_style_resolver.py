"""Pure domain-fact to display-style mapping; no names or compound type tests."""
CLASS_LABELS = {'high_speed': '高速铁路', 'conventional': '普速铁路', 'freight': '货运铁路',
                'metro': '地铁', 'industrial': '工业铁路', 'unknown': '类别待核实'}
ROLE_LABELS = {'main_track': '正线', 'arrival_departure_track': '到发线',
    'shunting_track': '调车线', 'lead_track': '牵出线', 'freight_track': '货物线',
    'depot_track': '段管线', 'locomotive_running_track': '机车走行线',
    'maintenance_track': '检修线', 'stabling_track': '存车线', 'turnback_track': '折返线',
    'crossover': '渡线', 'spur_track': '支接用途轨道', 'safety_siding': '安全线',
    'escape_siding': '避难线', 'other_station_track': '其他站线', 'unknown': '用途待核实'}
LINE_ROLE_LABELS = {'main_line': '干线', 'branch_line': '支线', 'connecting_line': '联络线',
                    'dedicated_line': '专用线', 'industrial_line': '工业线', 'unknown': '网络角色待核实'}

_LEGACY_CLASSES = {'high_speed': ('高速铁路线', '高速铁路站场股道'),
    'conventional': ('普速铁路线', '普速铁路站场股道'), 'freight': ('货运铁路线', '货运站场股道'),
    'metro': ('未确认类型', '站场股道（类型待核对）'),
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


def style_key(facts):
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


def semantic_summary(facts):
    return ' · '.join((CLASS_LABELS.get(facts.get('railway_class'), '类别待核实'),
                     ROLE_LABELS.get(facts.get('track_role'), '用途待核实')))
