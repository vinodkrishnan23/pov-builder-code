"""Typed Pydantic models — the contract between graph nodes (new.md's
"DESIGN PRINCIPLE": avoid passing arbitrary dictionaries between agents).

None of these hold generated source code — only references, metadata,
status, commit SHAs, and validation results, per new.md's Phase 0 STATE
instruction. Source code lives in the POV's Git repository.
"""
