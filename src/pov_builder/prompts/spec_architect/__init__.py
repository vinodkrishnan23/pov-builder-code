"""new.md Phase 3 — spec_architect, implemented as 4 sequential design
stages plus a finishing synthesis, rather than one monolithic LLM call.

Each stage consumes the PREVIOUS stage's REAL output (not the whole thing
re-invented at once), which is the whole point: a single call asked to
produce data_model/api_contract/frontend_contract/requirements together
risks the API contract not quite matching the data model, since nothing
forces one to be grounded in the other — they're all invented in the same
breath. Sequencing them forces each artifact to actually be built on the
last one, not just alongside it.

    schema_design
        |  (real data_model)
    query_pattern_design
        |  (real data_model + query_patterns)
    api_contract_design
        |  (real data_model + query_patterns + api_contract)
    frontend_contract_design
        |  (all four real outputs)
    finishing  — synthesizes system_architecture, ai_agent_workflows,
                 requirements.json, environment/integration_requirements
                 from the complete picture; the only stage that CAN
                 ground these in what was actually designed, since it's
                 the only one that sees all four real outputs at once.

Each stage's `*Output` schema is transient (LLM structured output only,
never part of POVState) — same reasoning as the old single-stage design:
`ContractRef`s need a real commit sha, which doesn't exist until AFTER
`graph/nodes.py`'s `make_spec_architect` commits each stage's raw content.
"""
