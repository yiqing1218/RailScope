from desktop.operating import Plan
from desktop.geometry import distance_m


def test_motion_follows_corners_and_holds_during_dwell():
    from desktop.vehicle_motion import motion_frames
    coordinates = [[120, 30], [120.001, 30], [120.001, 30.001]]
    mid = distance_m(*coordinates[:2])
    end = mid + distance_m(*coordinates[1:])
    path = {'coordinates': coordinates, 'cumulative': [0, mid, end], 'length_m': end}
    mid, end = path['cumulative'][1], path['length_m']
    plan = Plan([{'id': 'line', 'path': path}])
    plan.trains = [{'id': 'T1', 'line_id': 'line', 'enabled': True, 'stops': [
        {'arrival_s': 0, 'departure_s': 0, 'distance_m': 0, 'station_id': 'a'},
        {'arrival_s': 1, 'departure_s': 2, 'distance_m': mid, 'station_id': 'b'},
        {'arrival_s': 3, 'departure_s': 3, 'distance_m': end, 'station_id': 'c'}]}]
    frames = motion_frames(plan, plan.trains[0], path, 0, 0, 6, True)
    assert [0, 120, 30] == frames[0]
    assert any(abs(t - 1000/6) < .01 and [x,y] == [120.001,30] for t,x,y in frames)
    assert any(abs(t - 2000/6) < .01 and [x,y] == [120.001,30] for t,x,y in frames)
    assert frames[-1][1:] == [120.001,30.001]
    assert motion_frames(plan, plan.trains[0], path, 0, 0, 6, False) == [[0, 120, 30]]
