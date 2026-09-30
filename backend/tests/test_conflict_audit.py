from railscope.domain import HeadwayRule, TrackOccupancy
from railscope.repository import RailRepository
from railscope.services.conflicts import detect_conflicts


def test_default_headway_rule_applies_without_block_override():
    repo = RailRepository()
    repo.headway_rules = [HeadwayRule('default', 300, 300)]
    repo.occupancies = [TrackOccupancy('a', 'A', 'block', 'B', 0, 10, 'forward', 'S'),
                        TrackOccupancy('b', 'B', 'block', 'B', 240, 250, 'forward', 'S')]
    assert [c.conflict_type for c in detect_conflicts(repo, 'S')] == ['headway_violation']


def test_independent_resources_do_not_generate_pair_comparisons(monkeypatch):
    comparisons = []
    class ResourceId(str):
        __hash__ = str.__hash__
        def __eq__(self, other):
            comparisons.append(1)
            return super().__eq__(other)
        def __ne__(self, other):
            comparisons.append(1)
            return super().__ne__(other)
    repo = RailRepository()
    repo.occupancies = [TrackOccupancy(str(i), str(i), 'block', ResourceId(str(i)), 0, 10, 'forward', 'S')
                        for i in range(100)]
    def unrelated(*_):
        raise AssertionError('Independent resources must not be compared')
    monkeypatch.setattr('railscope.services.conflicts._overlap', unrelated)
    assert detect_conflicts(repo, 'S') == []
    assert len(comparisons) < 300
