"""Evidence-based track roles; planning corridors never inferred from geography."""

CORRIDORS = (
    "纵向 · 沿海通道",
    "纵向 · 京沪通道",
    "纵向 · 京港台通道",
    "纵向 · 京哈—京港澳通道",
    "纵向 · 呼南通道",
    "纵向 · 京昆通道",
    "纵向 · 包（银）海通道",
    "纵向 · 兰（西）广通道",
    "横向 · 绥满通道",
    "横向 · 京兰通道",
    "横向 · 青银通道",
    "横向 · 陆桥通道",
    "横向 · 沿江通道",
    "横向 · 沪昆通道",
    "横向 · 厦渝通道",
    "横向 · 广昆通道",
)
TRACK_TYPES = (
    "高速铁路线",
    "普速铁路线",
    "货运铁路线",
    "联络线 / 匝道",
    "支线 / 岔道",
    "渡线 / 道岔连接轨",
    "高速铁路站场股道",
    "普速铁路站场股道",
    "货运站场股道",
    "站场股道（类型待核对）",
    "车辆段 / 检修线",
    "折返线",
    "未确认类型",
)


def track_type(tags):
    name = tags.get("name", "") + " " + tags.get("project:name", "")
    highspeed = tags.get("highspeed") == "yes" or (
        tags.get("highspeed") != "no" and any(s in name for s in ("高铁", "高速铁路"))
    )
    freight = (
        tags.get("usage") in ("industrial", "freight") or tags.get("passenger") == "no"
    )
    try:
        speed = float(tags.get("maxspeed", "").split()[0])
    except (ValueError, IndexError):
        speed = None
    ordinary = tags.get("highspeed") == "no" or (
        tags.get("usage") in ("main", "branch") and speed is not None and speed <= 160
    )
    service = tags.get("service", "")
    if service == "crossover":
        return "渡线 / 道岔连接轨", "service=crossover"
    if service in ("yard", "siding"):
        role = (
            "高速铁路"
            if highspeed
            else "货运"
            if freight
            else "普速铁路"
            if ordinary
            else ""
        )
        return (
            role + "站场股道" if role else "站场股道（类型待核对）",
            "service=" + service,
        )
    if any(
        s in name for s in ("动车段", "车辆段", "检修", "机务段", "动车走行", "动走线")
    ):
        return "车辆段 / 检修线", "OSM 名称中的段场/检修用途"
    if any(s in name for s in ("折返", "立折")):
        return "折返线", "OSM 名称中的折返用途；尚未核验联锁进路"
    if any(s in name for s in ("渡线", "渡A线", "渡B线")):
        return "渡线 / 道岔连接轨", "OSM 名称中的渡线用途"
    if any(s in name for s in ("联络", "疏解")):
        return "联络线 / 匝道", "OSM 名称中的联络用途；不等于已验证的车次进路"
    if service == "spur":
        return "支线 / 岔道", "service=spur"
    if freight:
        return "货运铁路线", "usage=industrial/freight 或 passenger=no"
    if highspeed:
        return "高速铁路线", "highspeed=yes 或明确高速铁路名称"
    if ordinary:
        return "普速铁路线", "highspeed=no 或 main/branch 且标注速度≤160"
    return "未确认类型", "缺少可确认用途/速度标签；不靠外观猜测"


def corridor_for(name, category):
    if category != "高速铁路线":
        return "不适用"
    # Named sections along the NDRC planning corridors, not fuzzy/geographic guesses.
    # Common/partial alignments and unnamed tracks still require review.
    for aliases, corridor in (
        (("杭深线", "甬广高速线", "盐通高速线", "青盐线", "沪苏通线"), CORRIDORS[0]),
        (
            (
                "京沪高速铁路",
                "京沪高速线",
                "京沪高铁",
                "宁杭高速线",
                "合蚌客专线",
                "合杭高速线",
            ),
            CORRIDORS[1],
        ),
        (("京港高速线", "合福高速线", "昌福线", "阜黄高速线"), CORRIDORS[2]),
        (
            (
                "京广高速铁路",
                "京广高速线",
                "京广高铁",
                "京哈高速铁路",
                "京哈高速线",
                "京哈高铁",
                "沈大高速线",
                "广深港高速线",
            ),
            CORRIDORS[3],
        ),
        (("郑太客专线", "邵永高速线", "宜常高速线"), CORRIDORS[4]),
        (("西成客专线", "渝昆高速线", "渝昆高铁", "张大客专线"), CORRIDORS[5]),
        (
            (
                "银西高速线",
                "西渝高速线",
                "西渝高铁",
                "贵南高速线",
                "海南东环高速线",
                "海南西环高速线",
            ),
            CORRIDORS[6],
        ),
        (("兰渝线", "成贵客专线", "贵广客专线", "川青线", "川青铁路"), CORRIDORS[7]),
        (("哈牡客专线", "哈齐客专线"), CORRIDORS[8]),
        (("京包客专线", "包银高速线", "银兰高速线"), CORRIDORS[9]),
        (("济青高速线", "石济客专线", "石太客专线"), CORRIDORS[10]),
        (
            ("徐兰高速铁路", "徐兰高速线", "徐兰高铁", "徐连高速线", "兰新客专线"),
            CORRIDORS[11],
        ),
        (
            (
                "沪渝蓉高速铁路",
                "沪渝蓉高速线",
                "沪渝蓉高铁",
                "沪蓉线",
                "宁安客专线",
                "武九客专线",
                "渝万高速线",
                "成达万高速铁路",
            ),
            CORRIDORS[12],
        ),
        (("沪昆高速铁路", "沪昆高速线", "沪昆高铁", "沪杭高速线"), CORRIDORS[13]),
        (("渝厦高速线", "黔张常线", "赣瑞龙线", "龙漳线"), CORRIDORS[14]),
        (("南广高速线", "南广线", "南昆客专线"), CORRIDORS[15]),
    ):
        if any(alias in name for alias in aliases):
            return corridor
    return "高速通道待核对"


CONVENTIONAL_LINE_KEYWORDS = (
    '京哈', '京沪', '京广', '京九', '沪昆', '陇海', '兰新', '京包', '包兰',
    '滨洲', '滨绥', '沈山', '京承', '京通', '京原', '丰沙', '石太', '胶济',
    '焦柳', '宁西', '西康', '成昆', '成渝', '宝成', '湘桂', '黔桂', '黎湛',
    '南昆', '鹰厦', '青藏', '兰青', '新长', '淮南', '襄渝', '浙赣', '沪杭',
    '津浦', '太焦', '新兖', '胶新', '蓝烟', '合九', '皖赣', '宁芜', '沪宁',
    '同蒲', '京山', '津山', '德大', '邯济', '邯黄', '邢和', '邢黄',
)
FREIGHT_LINE_KEYWORDS = (
    '大秦', '朔黄', '侯月', '兖石', '唐包', '蒙华', '浩吉', '瓦日',
)


def catalog_parents(meta, mode):
    """Classify a physical line without mistaking geometry fragments for routes.

    Unknown speed and geography stay explicit so directory labels never claim
    facts absent from source tags or reviewed workspace metadata.
    """
    try:
        from .rail_semantics import semantic_record
        from .rail_style_resolver import ROLE_LABELS
    except ImportError:
        from rail_semantics import semantic_record
        from rail_style_resolver import ROLE_LABELS
    facts = semantic_record(meta)
    railway_class, line_role, role = (facts[key] for key in ('railway_class','line_role','track_role'))
    name = (
        meta.get("line_name")
        or meta.get("line_display_name")
        or meta.get("name")
        or "未命名物理线路"
    ).split(" · ", 1)[0]
    prefix = ("在建铁路",) if meta.get("construction") else ()
    region = _rail_region(meta)
    track_kind = str(meta.get("track_type") or "")
    mixed_evidence = "多种可追溯分类依据" in str(meta.get("type_evidence") or "")
    # 站场股道 / 设施轨道的旧版 track_type 也是设施信号，不能只依赖语义 track_role。
    station_track_types = {
        '高速铁路站场股道', '普速铁路站场股道', '货运站场股道', '站场股道（类型待核对）',
        '车辆段 / 检修线', '折返线', '渡线 / 道岔连接轨',
    }
    station_name_hints = ('进站线', '出站线', '到发线', '发车线', '环到线', '环发线',
                          '到达线', '牵出线', '走行线', '机走线', '机待线', '整备线')
    if (facts.get('facility_only') or role not in ('main_track', 'unknown')
            or track_kind in station_track_types
            or any(word in name for word in station_name_hints)):
        return prefix + ('车站设施', meta.get('station_name') or facts.get('facility_id') or '未关联设施',
                         ROLE_LABELS.get(role, '用途待核实'))
    # 名称中的“联络 / 疏解”是比 usage 推断的 line_role 更可靠的联络线信号。
    if (line_role == 'connecting_line'
            or any(word in name for word in ('联络', '疏解', '联线', '货联', '下联', '上联', '联结线'))):
        if railway_class == 'high_speed':
            return prefix + ('联络线', '高速联络线')
        if railway_class == 'conventional':
            return prefix + ('联络线', '普速联络线')
        return prefix + ('联络线', '速度待核实的联络线')
    if any(word in name for word in ("地方铁路", "地方线")):
        return prefix + ("其他铁路", "地方铁路")
    if any(word in name for word in ("矿区", "矿山")):
        return prefix + ("其他铁路", "矿区铁路")
    if any(word in name for word in ("港口", "港区", "码头")):
        return prefix + ("其他铁路", "港口铁路")
    if not mixed_evidence and railway_class == 'unknown':
        if track_kind == '普速铁路线':
            railway_class = 'conventional'
        elif track_kind == '高速铁路线':
            railway_class = 'high_speed'
        elif track_kind == '货运铁路线':
            railway_class = 'freight'
    if railway_class == 'unknown':
        # 明确名称优先于未知速度：高速/高铁 > 货运专线 > 普速干线。
        if any(word in name for word in ('高速', '高铁')):
            railway_class = 'high_speed'
        elif any(word in name for word in FREIGHT_LINE_KEYWORDS) or any(
                word in name for word in ('货车', '煤码头', '货线')):
            railway_class = 'freight'
        elif any(word in name for word in CONVENTIONAL_LINE_KEYWORDS):
            railway_class = 'conventional'
    if railway_class == 'unknown' and str(meta.get('service_type')) == 'industrial':
        return prefix + ('货运与专用铁路', region, '工业专用线')
    if railway_class == 'unknown' and (name.startswith('未命名') or name.startswith('站线（用途待核实）')):
        return prefix + ('待归属物理轨道', region, '线路归属待核实')
    if railway_class == 'high_speed':
        trunk = any(word in name for word in ("京沪", "京广", "沪昆", "徐兰", "京哈", "京港", "沿海", "陆桥"))
        technical = meta.get("technical_attributes") or {}
        speed = meta.get("design_speed_kmh") or technical.get("design_speed_kmh") or meta.get("maxspeed")
        try:
            speed = int(str(speed).replace("km/h", "").strip())
        except (ValueError, TypeError):
            speed = None
        band = str(speed) if speed in (350, 250, 200) else "其他/速度待核对"
        return prefix + ("高速铁路", "干线" if trunk else region, band)
    if railway_class in {'conventional', 'freight'}:
        trunk = any(word in name for word in ("京沪", "京广", "陇海", "沪昆", "京九", "兰新"))
        service = "货运" if railway_class == 'freight' else meta.get("service_type", "客货运")
        if service not in {"客运", "货运", "客货运"}:
            service = "客货运"
        return prefix + ("普速铁路", "干线" if trunk else region, service)
    return prefix + ("其他铁路", "类型待核实")


def _rail_region(meta):
    provinces = set(meta.get("provinces") or ())
    if not provinces and meta.get("province"):
        provinces.add(meta["province"])
    regions = {
        "华北": ("北京", "天津", "河北", "山西", "内蒙古"),
        "东北": ("辽宁", "吉林", "黑龙江"),
        "华东": ("上海", "江苏", "浙江", "安徽", "福建", "江西", "山东"),
        "中南": ("河南", "湖北", "湖南", "广东", "广西", "海南"),
        "西南": ("重庆", "四川", "贵州", "云南", "西藏"),
        "西北": ("陕西", "甘肃", "青海", "宁夏", "新疆"),
    }
    found = {region for province in provinces for region, names in regions.items() if province.startswith(names)}
    return next(iter(found)) if len(found) == 1 else "区域待核对"
