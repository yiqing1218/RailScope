"""Short display forecasts on the same measured polyline and timetable as Plan."""
from bisect import bisect_left, bisect_right

try:
    from .geometry import interpolate
except ImportError:
    from geometry import interpolate


def motion_frames(plan, train, path, clock, distance, speed, playing, horizon=.5):
    frames = [[0, *interpolate(path, distance)]]
    if not playing or speed <= 0:
        return frames
    end = min(clock + horizon * speed, train['stops'][-1]['departure_s'])
    if end <= clock:
        return frames
    times = sorted({end, *(s[key] for s in train['stops'] for key in ('arrival_s', 'departure_s')
                           if clock < s[key] < end)})
    previous_time, previous_distance = clock, distance
    cumulative = path['cumulative']
    for time in times:
        position = plan.position(train['id'], time)
        if position is None:
            break
        target = position['distance_m']
        lo, hi = sorted((previous_distance, target))
        indices = list(range(bisect_right(cumulative, lo), bisect_left(cumulative, hi)))
        if target < previous_distance:
            indices.reverse()
        for i in indices:
            fraction = (cumulative[i] - previous_distance) / (target - previous_distance)
            frame_time = previous_time + (time - previous_time) * fraction
            frames.append([(frame_time - clock) / speed * 1000, *path['coordinates'][i]])
        frames.append([(time - clock) / speed * 1000, *interpolate(path, target)])
        previous_time, previous_distance = time, target
    return frames
