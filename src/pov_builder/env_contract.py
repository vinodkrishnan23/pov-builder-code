"""Standard environment-variable contract between `integration_validator`
and every code-generation phase (seeder/backend_dev/frontend_dev).

Fixes a real, repeatedly-observed bug: generated code invented its own
names for the same standard values (`MONGODB_DATABASE` vs `MONGODB_DB` vs
`MONGODB_DB_NAME`, `DATASET_SIZE` vs `SEED_DATASET_SIZE`...) across
different generations of the SAME component. `integration_validator` could
never reliably guess which name a given script would use, so it only ever
supplied `MONGODB_URI` — and any script needing the bare database name
separately (which every real generation did) crashed immediately with
"missing required environment variable", every single time, completely
unfixable by any repair loop since the generated code was declaring a
perfectly reasonable dependency that the harness just never supplied.

Fix: name these ONCE, here, and require every code-generation prompt to
use EXACTLY these names — `integration_validator` then always supplies
exactly this same fixed set, no guessing needed on either side.
"""

from __future__ import annotations

MONGODB_URI = "MONGODB_URI"
MONGODB_DB = "MONGODB_DB"  # matches this project's OWN Settings.mongodb_db / .env's MONGODB_DB convention
PORT = "PORT"
API_BASE_URL = "API_BASE_URL"
# Vite (frontend_dev's stated safe default, and what it's actually
# generated in practice — confirmed live, a real `vite` dev server was
# seen running) only exposes env vars prefixed VITE_ to client-side code
# via `import.meta.env` — a plain API_BASE_URL is invisible to Vite-built
# code. Supplying BOTH means the frontend works whether it reads
# `import.meta.env.VITE_API_BASE_URL` (Vite) or `process.env.API_BASE_URL`
# (any other tooling) — not guessing, just covering the one real,
# well-known framework convention split that actually applies here.
VITE_API_BASE_URL = "VITE_API_BASE_URL"
# Always supplied as "false" by integration_validator — validation never
# exercises an authenticated flow, so a backend that defaults to requiring
# auth would otherwise fail health/API checks for reasons unrelated to
# code quality.
REQUIRE_AUTH = "REQUIRE_AUTH"
# Belt-and-suspenders alongside REQUIRE_AUTH: a real generation declared
# `ADMIN_TOKEN` as a hard-checked value regardless of any "require auth"
# flag — if a generation does the same and checks this unconditionally, a
# real (if meaningless-for-validation) value here means it still passes
# rather than crashing/401ing on an unset token.
AUTH_TOKEN = "AUTH_TOKEN"

# Seeder-only knobs — hard contract too (not just optional-with-defaults):
# integration_validator always supplies REAL values for these, so a
# generated seed script can rely on them being present and correct rather
# than guessing at its own default. DROP_EXISTING_COLLECTIONS in
# particular closes off a real class of bug: a script that assumes a
# default that doesn't match what integration_validator actually does.
DATASET_SIZE = "DATASET_SIZE"
RANDOM_SEED = "RANDOM_SEED"
DROP_EXISTING_COLLECTIONS = "DROP_EXISTING_COLLECTIONS"
ATLAS_SEARCH_INDEX_WAIT_MS = "ATLAS_SEARCH_INDEX_WAIT_MS"
# Distinct from ATLAS_SEARCH_INDEX_WAIT_MS (a duration) — a real
# generation used a separate boolean toggle (`ATLAS_SEARCH_INDEXES`) for
# WHETHER to create search/vector indexes at all.
CREATE_SEARCH_INDEXES = "CREATE_SEARCH_INDEXES"

_DESCRIPTIONS = {
    MONGODB_URI: "full MongoDB connection string, already scoped to this POV's database",
    MONGODB_DB: "the bare database name, if you need it separately from the URI "
    "(e.g. `client.db(process.env.MONGODB_DB)`)",
    PORT: "the port this process must listen on",
    API_BASE_URL: "the backend's base URL — where to send API requests "
    "(read via `process.env.API_BASE_URL` for non-Vite tooling)",
    VITE_API_BASE_URL: "the SAME backend base URL, under the name Vite actually exposes to client code — "
    "if you're using Vite (the safe default), read this one via `import.meta.env.VITE_API_BASE_URL`, "
    "NOT `API_BASE_URL` (Vite never exposes an unprefixed env var to client-side code)",
    REQUIRE_AUTH: '"false" — validation always runs unauthenticated; do not require auth for any '
    "endpoint the validator will call (health checks, the primary user journey)",
    AUTH_TOKEN: "a fixed placeholder token — only meaningful if you check it unconditionally "
    "regardless of REQUIRE_AUTH; prefer gating on REQUIRE_AUTH instead",
    DATASET_SIZE: 'one of "small", "medium", or "large" — how much synthetic data to generate',
    RANDOM_SEED: "a deterministic seed string — use it to seed your RNG so re-running produces the same dataset",
    DROP_EXISTING_COLLECTIONS: '"true" or "false" — whether to drop each collection before repopulating it '
    "(always supplied as a real value, not left to your own default, so re-seeding the SAME database is safe)",
    ATLAS_SEARCH_INDEX_WAIT_MS: "milliseconds to wait after creating an Atlas Search/Vector Search index "
    "before using it (index build is asynchronous)",
    CREATE_SEARCH_INDEXES: '"true" or "false" — whether to create Atlas Search/Vector Search indexes at all '
    "(separate from ATLAS_SEARCH_INDEX_WAIT_MS, which only controls how long to wait after creating them)",
}

# Every generated seed/backend/frontend gets exactly this set — see
# build_messages in prompts/seeder.py, backend_dev.py, frontend_dev.py.
# This IS the complete, exhaustive list each component may read from the
# environment — see build_env_contract_section's closing instruction: no
# other environment variable is permitted, not even with a fallback
# default, so there is exactly one place left to ever add a new one.
SEEDER_ENV_VARS = [
    MONGODB_URI,
    MONGODB_DB,
    DATASET_SIZE,
    RANDOM_SEED,
    DROP_EXISTING_COLLECTIONS,
    ATLAS_SEARCH_INDEX_WAIT_MS,
    CREATE_SEARCH_INDEXES,
]
BACKEND_ENV_VARS = [MONGODB_URI, MONGODB_DB, PORT, REQUIRE_AUTH, AUTH_TOKEN]
FRONTEND_ENV_VARS = [PORT, API_BASE_URL, VITE_API_BASE_URL]

# Fixed values integration_validator actually supplies for the seeder-only
# knobs above — deliberately small/deterministic, since this is an
# automated validation run, not the final human-facing demo seed (a human
# re-seeding for real later can choose differently by hand).
SEEDER_VALIDATION_DEFAULTS = {
    DATASET_SIZE: "small",
    RANDOM_SEED: "pov-builder-validation",
    DROP_EXISTING_COLLECTIONS: "true",
    ATLAS_SEARCH_INDEX_WAIT_MS: "5000",
    CREATE_SEARCH_INDEXES: "true",
}

# Same idea for backend — REQUIRE_AUTH is always "false" during
# validation, and AUTH_TOKEN is a fixed placeholder in case a generation
# checks it unconditionally regardless of REQUIRE_AUTH.
BACKEND_VALIDATION_DEFAULTS = {
    REQUIRE_AUTH: "false",
    AUTH_TOKEN: "pov-builder-validation-token",
}


def build_env_contract_section(applicable_vars: list[str]) -> str:
    """Appended to a code-generation SYSTEM_PROMPT. `applicable_vars` is
    the subset of the standard names relevant to that component (e.g.
    seeder needs Mongo access but never a PORT; frontend needs PORT/
    API_BASE_URL but never direct Mongo access).

    Deliberately a HARD "only these names, nothing else" rule, not "these
    are guaranteed, anything extra needs a safe default" — the softer
    version still left room for a generation to invent its own additional
    name (e.g. ADMIN_TOKEN) that the harness could never anticipate. If a
    real need surfaces that isn't covered here, the fix is to add it to
    this module, not to let generations route around the contract."""
    lines = "\n".join(f"- `{name}`: {_DESCRIPTIONS[name]}" for name in applicable_vars)
    return (
        "\n\n## Standard environment variables — the ONLY ones you may use\n"
        "The validation harness supplies EXACTLY these environment variables for "
        "this component, and no others:\n"
        f"{lines}\n\n"
        "Use these names verbatim wherever you need the value they cover — never "
        "invent an alternate name for the same thing. Do NOT read any OTHER "
        "environment variable for ANY purpose. If you need configuration this list "
        "doesn't cover, hard-code a sensible constant directly in your code instead "
        "of introducing a new environment variable — the harness will not supply it."
    )
