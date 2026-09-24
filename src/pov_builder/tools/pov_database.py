"""PovDatabaseStore — provisions/tears down a per-POV MongoDB Atlas
database for `integration_validator` (new.md Phase 7) to seed and query
against for real, rather than validating against nothing.

ONE shared Atlas cluster for everything (same constraint as `MONGODB_URI`
everywhere else in this codebase) — a uniquely-named DATABASE per POV,
not a separate cluster. `teardown` is always called by
`integration_validator` right after checks complete, pass or fail — this
is test-fixture-style provisioning, not a persistent resource.
"""

from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from pov_builder.config.settings import Settings, load_settings


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "pov"


def _db_name(email: str, pov_name: str) -> str:
    # Mongo database names: no `/\. "$*<>:|?`, and practically capped well
    # under 64 bytes — truncate each slugged segment defensively rather
    # than trusting email/pov_name to stay short.
    return f"pov_{_slugify(email)[:20]}_{_slugify(pov_name)[:20]}"


def _uri_with_db(uri: str, db_name: str) -> str:
    parts = urlsplit(uri)
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}", parts.query, parts.fragment))


class PovDatabaseStore(Protocol):
    def provision(self, *, email: str, pov_name: str) -> tuple[str, str]:
        """Returns (mongo_uri_for_that_db, db_name). Idempotent — the same
        (email, pov_name) always resolves to the same db_name, matching
        the git branch reuse convention (see tools/git_repo.py)."""
        ...

    def teardown(self, db_name: str) -> None:
        """Drops the database. Safe to call even if it was never seeded."""
        ...


class MongoPovDatabaseStore:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or load_settings()
        self._client = None  # constructed lazily — same reasoning as MongoPovRunStore

    def _mongo_client(self):
        if self._client is None:
            from pymongo import MongoClient

            self._client = MongoClient(self._settings.mongodb_uri)
        return self._client

    def provision(self, *, email: str, pov_name: str) -> tuple[str, str]:
        db_name = _db_name(email, pov_name)
        return _uri_with_db(self._settings.mongodb_uri, db_name), db_name

    def teardown(self, db_name: str) -> None:
        self._mongo_client().drop_database(db_name)
