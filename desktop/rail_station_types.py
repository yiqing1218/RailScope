"""Evidence-based presentation types for physical railway stations/facilities.

This vocabulary is an import/catalog adapter, not another domain model.
References and boundaries remain those of the shared infrastructure repository.
"""

STATION_TYPES = (
    '客运站', '货运站', '客货运站', '编组站', '区段站', '中间站',
    '线路所', '信号所', '乘降所', '越行站', '会让站',
    '工业站', '港湾站', '国境站', '换装站', '集装箱办理站',
    '动车段', '动车所', '客车整备所', '动车所/客整所',
    '货场', '车场', '到达场', '出发场', '到发场', '调车场', '存车场', '整备场',
    '车辆段', '客车车辆段', '货车车辆段', '机务段', '机务折返段', '机车整备所',
    '检修站', '整修站', '检修所', '站修所', '列检所',
    '工务段', '电务段', '供电段', '综合维修段', '未定义',
)
FACILITY_TYPES = frozenset(STATION_TYPES) - {
    '客运站', '货运站', '客货运站', '区段站', '中间站',
    '线路所', '信号所', '乘降所', '越行站', '会让站', '国境站', '未定义',
}
STATION_KINDS = ('station', 'halt', 'signal_box', 'junction',
                 'yard', 'depot', 'workshop', 'works', 'engine_shed')

# Specific terms precede generic 段/场 terms. The combined historical label is
# still accepted for saved manual overrides; new evidence distinguishes them.
NAME_TYPES = (
    ('动车所', ('动车运用检修所', '动车运用所', '动车检修所', '动车所')),
    ('动车段', ('动车段',)),
    ('客车整备所', ('客车技术整备所', '客车整备所', '客整所')),
    ('机车整备所', ('机车整备所', '机务整备所')),
    ('机务折返段', ('机务折返段', '机务折返所', '机车折返段')),
    ('客车车辆段', ('客车车辆段',)),
    ('货车车辆段', ('货车车辆段',)),
    ('车辆段', ('车辆段', '车辆基地')),
    ('机务段', ('机务段',)),
    ('站修所', ('站修所',)), ('列检所', ('列检所', '列车检验所')),
    ('整修站', ('整修站',)), ('检修所', ('检修所',)),
    ('检修站', ('检修站', '检修段', '检修基地', '维修基地')),
    ('综合维修段', ('综合维修段',)),
    ('工务段', ('工务段',)), ('电务段', ('电务段',)), ('供电段', ('供电段',)),
    ('编组站', ('编组站', '编组场')), ('区段站', ('区段站',)),
    ('越行站', ('越行站',)), ('会让站', ('会让站',)),
    ('中间站', ('中间站',)), ('线路所', ('线路所',)), ('信号所', ('信号所',)),
    ('乘降所', ('乘降所',)), ('集装箱办理站', ('集装箱办理站', '集装箱中心站')),
    ('国境站', ('国境站', '口岸站')), ('换装站', ('换装站',)),
    ('港湾站', ('港湾站', '港前站')), ('工业站', ('工业站',)),
    ('到发场', ('到发场',)), ('到达场', ('到达场',)), ('出发场', ('出发场',)),
    ('调车场', ('调车场',)), ('存车场', ('存车场',)), ('整备场', ('整备场', '整备所')),
    ('货场', ('货场',)), ('车场', ('车场',)),
)
TAG_TYPES = {
    'classification_yard': '编组站', 'marshalling_yard': '编组站',
    'locomotive_depot': '机务段', 'depot': '车辆段', 'rolling_stock_depot': '车辆段',
    'maintenance': '检修站', 'repair': '检修站', 'workshop': '检修站',
    'stabling_yard': '存车场', 'stabling': '存车场',
    'freight_terminal': '货场', 'freight_yard': '货场',
}


def station_type(tags, kind=''):
    tags = tags or {}
    text = ' '.join(str(tags.get(key, '')) for key in
                    ('name', 'name:zh', 'official_name', 'railway:station_category'))
    for label, terms in NAME_TYPES:
        if label == '车场' and '停车场' in text and not (
                tags.get('railway') in STATION_KINDS or tags.get('railway:facility')
                or tags.get('landuse') == 'railway'):
            continue
        if any(term in text for term in terms):
            return label
    railway = tags.get('railway') or kind
    facility = tags.get('railway:facility', '')
    if facility in TAG_TYPES:
        return TAG_TYPES[facility]
    purpose = tags.get('railway:yard:purpose', '')
    if railway == 'yard':
        return {'maintenance': '检修站', 'intermodal': '集装箱办理站',
                'transloading': '货场', 'storage': '存车场', 'depot': '车辆段',
                'manifest': '调车场'}.get(purpose, '车场')
    if railway == 'depot':
        return '车辆段'
    if railway == 'engine_shed':
        return '机务段'
    if railway in ('workshop', 'works'):
        return '检修站'
    if railway == 'service_station':
        return '整备场'
    if railway == 'halt':
        return '乘降所'
    if railway == 'signal_box' or kind in ('signal_box', 'junction'):
        return '线路所'
    passenger, freight = tags.get('passenger'), tags.get('freight')
    if passenger == freight == 'yes':
        return '客货运站'
    if passenger == 'yes' and freight in ('no', None, ''):
        return '客运站'
    if freight == 'yes' and passenger in ('no', None, ''):
        return '货运站'
    return '未定义'


def station_point_kind(tags):
    """Normalise facility-only tags into existing station POI display kinds.

    Names alone may classify an already known rail object, but cannot import
    unrelated businesses as rail infrastructure.
    """
    railway = tags.get('railway', '')
    if railway in STATION_KINDS:
        return railway
    if railway == 'service_station':
        return 'workshop'
    rail_context = bool(tags.get('railway:facility') or tags.get('landuse') == 'railway'
                        or tags.get('building') in ('train_station', 'depot', 'service_station'))
    label = station_type(tags)
    if rail_context and railway not in ('rail', 'platform') and label in FACILITY_TYPES:
        return 'yard'
    return None
