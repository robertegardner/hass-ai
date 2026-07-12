from dataclasses import fields

from pae.db.models import Pattern
from pae.miner.types import PatternCandidate


def test_candidate_fields_match_pattern_columns():
    cols = {c.name for c in Pattern.__table__.columns}
    for f in fields(PatternCandidate):
        assert f.name in cols, f"PatternCandidate.{f.name} has no patterns column"
    assert {"id", "status", "mined_at"} <= cols


def test_pattern_key_is_unique():
    assert any(
        idx.unique and [c.name for c in idx.columns] == ["pattern_key"]
        for idx in Pattern.__table__.indexes
    )
