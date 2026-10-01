"""Shared validation of entity-owned presentation attributes."""
import re

SPEED_STANDARDS = (350, 250, 200, 160)


def valid_color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('颜色必须为 #RRGGBB')
    return value.lower()


def design_speed(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise ValueError('设计速度须为数值')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError('设计速度须为正数（km/h）') from None
    if not 0 < number <= 600 or not number.is_integer():
        raise ValueError('设计速度须为 1–600 的整数（km/h）')
    return int(number)


def source_design_speed(tags):
    """Unparseable source tags stay unknown; operating maxspeed is separate."""
    value = tags.get('designspeed') or tags.get('maxspeed:design')
    if value is None:
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.0+)?)\s*(?:km/h|kmh|kph)?\s*', str(value))
    if match:
        try:
            return design_speed(match[1])
        except ValueError:
            pass
    return None
