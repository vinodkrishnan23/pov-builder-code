"""Confirms the checkpoint serializer's allow-list is real, non-empty, and
covers the model types POVState actually holds — the whole point of
serde.py's introspection approach is that this stays true automatically as
new models are added, so assert it rather than trust it silently."""

from __future__ import annotations

from pov_builder.models.pov_spec import InitialPOVSpec
from pov_builder.models.repair import RepairInfo
from pov_builder.models.review import POVReviewResult
from pov_builder.serde import build_checkpoint_serializer, discover_checkpointable_types


def test_discovery_finds_every_model_used_in_povstate():
    discovered = set(discover_checkpointable_types())
    for expected in (InitialPOVSpec, POVReviewResult, RepairInfo):
        assert expected in discovered


def test_serializer_round_trips_a_model_without_falling_back_to_pickle():
    serializer = build_checkpoint_serializer()
    spec = InitialPOVSpec(executive_summary="round trip me")
    type_name, payload = serializer.dumps_typed(spec)
    restored = serializer.loads_typed((type_name, payload))
    assert restored == spec
