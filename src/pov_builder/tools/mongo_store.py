"""MongoDB Atlas persistence for pov-builder runs.

Not a new.md requirement — Phase 1's own scope is extraction only. This is
a workspace-specific addition: every real transcript_analyzer run persists
its transcript + resulting InitialPOVSpec, using the same Atlas cluster
chat-agent/spec-architect already use in this workspace (see
`.env.example`'s MONGODB_URI).

`MongoPovRunStore` connects LAZILY — building one never touches the
network, only calling `save_transcript_analysis` does. This matters: it
means constructing a store is safe even in placeholder-transcript runs
that have no real credentials configured, since that code path never
actually calls save (see `nodes.make_transcript_analyzer`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pov_builder.config.settings import Settings, load_settings
from pov_builder.models.pov_spec import InitialPOVSpec


class PovRunStore(Protocol):
    def save_transcript_analysis(
        self,
        *,
        user_email: str,
        pov_name: str,
        transcript: str,
        initial_spec: InitialPOVSpec,
        pov_id: str | None = None,
    ) -> str:
        """Persist one transcript_analyzer run. Mints a new pov_id UNLESS
        `pov_id` is given (a revise loop re-running transcript_analyzer
        after human feedback on the initial_spec approval gate — see
        graph/nodes.py), in which case it updates that same document
        in place rather than creating a second one. Returns the pov_id.

        `user_email`/`pov_name` are stored purely for traceability — the
        durable git/branch identity (see tools/git_repo.py); `pov_id`
        remains the sole key this store itself upserts by."""
        ...

    def save_spec_artifacts(self, *, pov_id: str, artifacts: dict[str, dict]) -> None:
        """Record where each spec_architect contract actually landed on
        GitHub — `artifacts` is `{name: {"path", "commit_sha", "url"}}`
        (e.g. `{"data_model": {...}}`). Queryable directly from
        `pov_builder_runs` without needing to load/deserialize the
        LangGraph checkpoint, where `technical_spec.data_model.path` would
        otherwise be the only place this information lives."""
        ...


class MongoPovRunStore:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or load_settings()
        self._client = None  # constructed lazily — see module docstring

    def _collection(self):
        if self._client is None:
            from pymongo import MongoClient

            self._client = MongoClient(self._settings.mongodb_uri)
        return self._client[self._settings.mongodb_db]["pov_builder_runs"]

    def save_transcript_analysis(
        self,
        *,
        user_email: str,
        pov_name: str,
        transcript: str,
        initial_spec: InitialPOVSpec,
        pov_id: str | None = None,
    ) -> str:
        is_revision = pov_id is not None
        pov_id = pov_id or str(int(datetime.now(timezone.utc).timestamp() * 1000))
        doc = {
            "pov_id": pov_id,
            "user_email": user_email,
            "pov_name": pov_name,
            "transcript": transcript,
            "initial_spec": initial_spec.model_dump(mode="json"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if is_revision:
            self._collection().update_one({"pov_id": pov_id}, {"$set": doc}, upsert=True)
        else:
            doc["created_at"] = doc["updated_at"]
            self._collection().insert_one(doc)
        return pov_id

    def save_spec_artifacts(self, *, pov_id: str, artifacts: dict[str, dict]) -> None:
        self._collection().update_one(
            {"pov_id": pov_id},
            {"$set": {"spec_artifacts": artifacts, "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
