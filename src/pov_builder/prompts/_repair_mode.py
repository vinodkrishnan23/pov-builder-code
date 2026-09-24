"""Shared repair-mode message section for seeder/backend_dev/frontend_dev.

Identical formatting logic for all three — factored out once rather than
copy-pasted, since a repair round means the same thing for each of them:
here's the code you ALREADY wrote, here's exactly what's wrong with it,
make the smallest correct change (new.md Phase 9's own words) rather than
regenerating from scratch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pov_builder.models.validation import ValidationCheck


def format_repair_mode_section(
    existing_files: dict[str, str] | None, failing_checks: "list[ValidationCheck] | None"
) -> str:
    """Returns "" when neither is given (the normal first-generation
    path) — callers append this directly to their message content."""
    if not existing_files and not failing_checks:
        return ""

    parts = [
        "\n\n## REPAIR MODE — you are fixing a REPORTED failure in EXISTING "
        "code, not writing from scratch"
    ]

    if existing_files:
        parts.append("\nCurrent committed implementation:")
        for path, content in existing_files.items():
            parts.append(f"\n--- {path} ---\n{content}")

    if failing_checks:
        parts.append("\n\nValidation failures reported against this exact implementation:")
        for check in failing_checks:
            parts.append(
                f"- [{check.category}] {check.description}\n"
                f"  evidence: {check.evidence}\n"
                f"  repair_action: {check.repair_action}"
            )

    parts.append(
        "\n\nMake the SMALLEST correct change that fixes these specific failures. "
        "Do NOT rewrite unrelated code or regenerate files that don't need to change. "
        "In `files`, return ONLY the files you actually changed, with their COMPLETE new content."
    )
    return "\n".join(parts)
