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


def catalog_parents(meta, mode):
    """Return the user-facing national line taxonomy.

    It classifies only from explicit line names/track roles.  Endpoint-delimited
    RS sections remain available in object details and Corridor editors.
    """
    category = meta.get("track_type", "未确认类型")
    name = (
        meta.get("line_name")
        or meta.get("line_display_name")
        or meta.get("name")
        or "未命名物理线路"
    ).split(" · ", 1)[0]
    compact = name.replace("高速线", "高速铁路").replace("高铁", "高速铁路")
    prefix = ("在建铁路",) if meta.get("construction") else ()
    if any(word in name for word in ("市域", "市郊")):
        city = next((word for word in ("上海", "北京", "成都") if word in name), "其他城市")
        return prefix + ("市域/市郊铁路", city)
    if (
        "城际" in name
        and category != "高速铁路线"
        and "高速铁路" not in compact
    ):
        region = (
            "长三角城际"
            if any(word in name for word in ("沪", "宁", "杭", "苏"))
            else "珠三角城际"
            if any(word in name for word in ("广", "珠", "莞", "佛"))
            else "京津冀城际"
            if any(word in name for word in ("京", "津", "石"))
            else "其他城际"
        )
        return prefix + ("城际铁路", region)
    if category == "高速铁路线" or "高速铁路" in compact:
        if any(word in compact for word in ("京沪高速铁路", "京广高速铁路", "沪昆高速铁路", "徐兰高速铁路")):
            return prefix + ("高速铁路", "国家高速铁路主干线")
        if any(word in name for word in ("沪宁城际", "宁杭", "杭甬", "广珠城际")):
            return prefix + ("高速铁路", "区域高速铁路")
        if category == "联络线 / 匝道" or any(word in name for word in ("联络线", "疏解线")):
            return prefix + ("高速铁路", "高速铁路联络线")
        return prefix + ("高速铁路", "区域高速铁路")
    if category in {"普速铁路线", "支线 / 岔道"}:
        if any(word in name for word in ("京沪铁路", "京沪线", "京广铁路", "京广线", "陇海", "沪昆铁路", "沪昆线", "京九")):
            return prefix + ("普速铁路", "国家铁路干线")
        if category == "支线 / 岔道" or "支线" in name:
            return prefix + ("普速铁路", "支线铁路")
        return prefix + ("普速铁路", "区域干线")
    if category == "货运铁路线":
        return prefix + ("其他铁路", "货运铁路")
    if any(word in name for word in ("矿区", "矿山")):
        return prefix + ("其他铁路", "矿区铁路")
    if any(word in name for word in ("港口", "港区", "码头")):
        return prefix + ("其他铁路", "港口铁路")
    return prefix + ("其他铁路", "不确定铁路")
