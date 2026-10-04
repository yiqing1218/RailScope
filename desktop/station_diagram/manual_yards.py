"""Resolve explicit diagram track numbers to stable StationTrack IDs."""

import re


def numbered_tracks(repo, rules, text):
    requested = set()
    text = re.sub(r"\s*([-~～—–至])\s*", r"\1", text.strip())
    for token in re.split(r"[,，、;；\s]+", text):
        if not token:
            continue
        token = token.removesuffix("道").upper()
        span = re.fullmatch(r"(\d+)\s*[-~～—–至]\s*(\d+)", token)
        if span:
            start, end = map(int, span.groups())
            if start > end or end - start > 500:
                raise ValueError("股道范围须从小到大，且不超过 500 道：" + token)
            requested.update(str(i) for i in range(start, end + 1))
        else:
            requested.add(token)
    if not requested:
        raise ValueError("先填写股道号，例如 1-4、7；无编号股道可在下表多选。")
    found, ids = set(), set()
    for track in repo.station_tracks.values():
        numbers = {
            str(track.track_number or "").strip().upper(),
            str(rules.get(track.id, {}).get("label", "")).strip().upper(),
        }
        matched = numbers & requested
        if matched:
            found.update(matched)
            ids.add(track.id)
    missing = requested - found
    if missing:
        raise ValueError(
            "未找到股道号：" + "、".join(sorted(missing)) + "；未修改任何分场。"
        )
    return ids
