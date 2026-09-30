"""Named railway termini for presentation, separate from operating topology.

Explicit workspace/OSM route termini take priority. This small reference table
supplies nominal cities only; it never extends a Corridor or certifies a route.
"""
import re

# Coordinates orient city labels in the diagram, never create infrastructure.
REFERENCE_TERMINALS = {
    '京沪': (('北京', (116.4, 39.9)), ('上海', (121.47, 31.23)),
        'user_requested_nominal_cities'),
    '京雄': (('北京', (116.4, 39.9)), ('雄安', (116.16, 39.05)),
        'https://www.xiongan.gov.cn/2019-09/21/c_1210288167.htm'),
    '京津': (('北京', (116.4, 39.9)), ('天津', (117.2, 39.1)),
        'https://www.nra.gov.cn/ztzl/hy/gsgt/'),
    '雄忻': (('雄安', (116.16, 39.05)), ('忻州', (112.73, 38.42)),
        'https://www.xiongan.gov.cn/2022-10/02/c_1211689867.htm'),
    '京港': (('北京', (116.4, 39.9)), ('香港', (114.17, 22.3)),
        'https://www.ndrc.gov.cn/xxgk/zcfb/ghwb/201607/W020190905497828820842.pdf'),
}


def nominal_terminals(name):
    """Match a complete railway name, not a connector containing its prefix."""
    for prefix, value in REFERENCE_TERMINALS.items():
        if re.fullmatch(prefix + r'(?:高速|高铁|城际)?(?:铁路|线)?', name):
            return value
    return None
