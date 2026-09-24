"""Standard `data-testid` contract between `frontend_dev` and the
primary-user-journey generator (`prompts/integration_validator.py`).

Mirrors `env_contract.py`'s fix for the exact same underlying problem, one
layer up: the journey-generation LLM call has never seen `frontend_dev`'s
actual rendered markup, so an invented selector (e.g.
`"h1, [data-testid='page-title']"`) has no guarantee of matching what got
built — a real failure hit live (a Playwright timeout waiting for a
guessed selector), unfixable by repairing `frontend_dev` 3 times since the
two LLM calls simply never shared a selector convention in the first place.

Fix, same shape as `env_contract.py`: a small set of UNIVERSAL testids
(present on every page, regardless of the POV) get fixed names
`frontend_dev` MUST use. Anything POV-specific (a search button, a results
table — there is no way to fix a universal name for these across every
possible POV) is instead REPORTED BACK by `frontend_dev` itself via
`FrontendDevOutput.key_element_testids`, so the journey generator can
ground its selectors in what was ACTUALLY built instead of guessing blind.

That fix alone still broke on a REAL follow-up case: a per-row element in
a list/table (e.g. "open this ticket") is naturally rendered with a
DIFFERENT testid for every row — there's no single fixed testid for it.
`key_element_testids` (built for a single, page-level element) can't
express that, so `frontend_dev` reported a literal templated string
(`"open-ticket-{ticketId}"`), and the journey step used it VERBATIM as a
CSS selector — matching nothing, since real rendered rows carry a real
id, never the literal string `{ticketId}`. `FrontendDevOutput` gained a
SECOND field, `list_item_testid_prefixes`, specifically for this case:
`frontend_dev` reports the STABLE PREFIX it renders onto every row (e.g.
`"open-ticket-"`), never a template with `{}` in it, and the journey
generator is told to build a `[data-testid^="prefix"]` ("starts with")
CSS selector — which Playwright's `page.click(...)` matches against the
first rendered instance, without either side ever needing to know a real
row's actual id.
"""

from __future__ import annotations

PAGE_TITLE = "page-title"
LOADING_INDICATOR = "loading-indicator"
ERROR_MESSAGE = "error-message"
EMPTY_STATE = "empty-state"

_DESCRIPTIONS = {
    PAGE_TITLE: "the main heading of EVERY page — exactly one per page, so a journey "
    "step can always confirm which page it landed on",
    LOADING_INDICATOR: "shown while a page/section is loading data",
    ERROR_MESSAGE: "shown when an API call fails",
    EMPTY_STATE: "shown when a list/search returns zero results",
}

UNIVERSAL_TESTIDS = [PAGE_TITLE, LOADING_INDICATOR, ERROR_MESSAGE, EMPTY_STATE]


def build_ui_contract_section() -> str:
    """Appended to `frontend_dev`'s SYSTEM_PROMPT."""
    lines = "\n".join(f'- `data-testid="{name}"`: {_DESCRIPTIONS[name]}' for name in UNIVERSAL_TESTIDS)
    return (
        "\n\n## Standard data-testid attributes — required on every page\n"
        "Add these EXACT `data-testid` values to the corresponding elements on EVERY "
        "page you build, so automated validation can reliably find them — never a "
        "different name or a generic tag-only selector for these:\n"
        f"{lines}\n\n"
        "For any OTHER interactive element central to a use case (a search button, a "
        "results list, a detail view, a form submit) — there is no universal name "
        "that fits every POV, so instead:\n\n"
        "- A SINGLE, page-level element (one instance per page — a search button, a "
        'form submit): give it a clear, stable `data-testid` of your own choosing, and '
        "report it in `key_element_testids` (a map from a short descriptive key like "
        '"search_button" to the exact testid you used, e.g. "ticket-search-btn").\n'
        "- A REPEATED, per-item element (one instance PER ROW in a list/table — e.g. "
        '"open this ticket" on every row): render a STABLE PREFIX plus the real item id '
        'as the testid (e.g. `data-testid="open-ticket-abc123"` for ticket abc123) — '
        "NEVER a literal template string with `{}` in it. Report just the prefix (e.g. "
        '"open-ticket-") in `list_item_testid_prefixes` (a map from a short descriptive '
        'key like "open_ticket_button" to that prefix). Downstream validation will '
        'match ANY row via a `[data-testid^="prefix"]` selector — it never needs, and '
        "will never guess, a specific item's real id."
    )


def format_declared_testids(
    key_element_testids: dict[str, str] | None, list_item_testid_prefixes: dict[str, str] | None = None
) -> str:
    """Appended to the journey-generation prompt — the ACTUAL POV-specific
    testids `frontend_dev` reported, grounding selector choices in what
    was really built instead of a guess."""
    universal = "\n".join(f'- `data-testid="{name}"`: {_DESCRIPTIONS[name]}' for name in UNIVERSAL_TESTIDS)
    section = (
        "\n\n## Available selectors — use ONLY these, never guess a different one\n"
        "Universal (present on every page):\n"
        f"{universal}\n"
    )
    if key_element_testids:
        declared = "\n".join(f'- "{key}" -> `data-testid="{testid}"`' for key, testid in key_element_testids.items())
        section += f"\nPOV-specific SINGLE elements frontend_dev actually built (key -> real testid):\n{declared}\n"
    if list_item_testid_prefixes:
        declared_prefixes = "\n".join(
            f'- "{key}" -> use selector `[data-testid^="{prefix}"]` (matches ANY row/item — '
            f"never invent or ask for a specific item's id)"
            for key, prefix in list_item_testid_prefixes.items()
        )
        section += (
            "\nPOV-specific REPEATED/list-item elements (one per row — use a "
            f"starts-with selector, matches the first one):\n{declared_prefixes}\n"
        )
    if not key_element_testids and not list_item_testid_prefixes:
        section += (
            "\nNo POV-specific element testids were declared — build the journey "
            "using ONLY the universal selectors above (e.g. confirm navigation via "
            "page-title), rather than guessing a selector for a specific button or list."
        )
    section += (
        "\nDo NOT invent a CSS selector, class name, or testid that isn't listed "
        "above, and do NOT use a literal template string containing `{}` as a "
        "selector — if the journey needs to interact with something not covered "
        "here, simplify the journey to use only what's available instead."
    )
    return section
