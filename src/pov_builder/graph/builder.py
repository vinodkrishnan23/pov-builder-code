"""Graph construction — new.md Phase 0's required topology, PLUS two human
approval gates (workspace-specific addition, not a new.md phase — see
README "Design decision: human approval gates"):

    START
      |
transcript_analyzer <---------------------------------.
      |                                                |
  pov_reviewer --(FAIL)--> END                         |
      |(PASS/PASS_WITH_WARNINGS)                        |
  initial_spec_approval_gate --(revise)------------------'
      |(approve)
  spec_architect <----------------------------------.
      |                                              |
  technical_spec_approval_gate --(revise)-------------'
      |(approve)
      | \\ \\
  seeder backend_dev frontend_dev   (parallel)
      | / /
integration_validator --(PASS)--> howto_helper --> END
      |(FAIL, repair_required)
  repair_router --(cap exceeded)--> END
      |(fan out to whichever of seeder/backend_dev/frontend_dev)
      \\--> back into integration_validator (loop)
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from pov_builder.graph import nodes, routing
from pov_builder.graph.state import POVState

NODE_NAMES = [
    "transcript_analyzer",
    "pov_reviewer",
    "initial_spec_approval_gate",
    "spec_architect",
    "technical_spec_approval_gate",
    "seeder",
    "backend_dev",
    "frontend_dev",
    "integration_validator",
    "repair_router",
    "howto_helper",
]


def build_graph(
    llm,
    pov_run_store,
    git_repo_store,
    checkpointer=None,
    shell_sandbox=None,
    pov_database=None,
    journey_runner=None,
):
    graph = StateGraph(POVState)

    graph.add_node("transcript_analyzer", nodes.make_transcript_analyzer(llm, pov_run_store))
    graph.add_node("pov_reviewer", nodes.make_pov_reviewer(llm))
    graph.add_node("initial_spec_approval_gate", nodes.initial_spec_approval_gate)
    graph.add_node("spec_architect", nodes.make_spec_architect(llm, git_repo_store, pov_run_store))
    graph.add_node("technical_spec_approval_gate", nodes.technical_spec_approval_gate)
    graph.add_node("seeder", nodes.make_seeder(llm, git_repo_store))
    graph.add_node("backend_dev", nodes.make_backend_dev(llm, git_repo_store))
    graph.add_node("frontend_dev", nodes.make_frontend_dev(llm, git_repo_store))
    graph.add_node(
        "integration_validator",
        nodes.make_integration_validator(llm, git_repo_store, shell_sandbox, pov_database, journey_runner),
    )
    graph.add_node("repair_router", nodes.repair_router)
    graph.add_node("howto_helper", nodes.make_howto_helper(llm, git_repo_store))

    graph.set_entry_point("transcript_analyzer")
    graph.add_edge("transcript_analyzer", "pov_reviewer")

    graph.add_conditional_edges(
        "pov_reviewer",
        routing.route_after_pov_reviewer,
        {"initial_spec_approval_gate": "initial_spec_approval_gate", "end": END},
    )

    graph.add_conditional_edges(
        "initial_spec_approval_gate",
        routing.route_after_initial_spec_gate,
        {"spec_architect": "spec_architect", "transcript_analyzer": "transcript_analyzer"},
    )

    graph.add_edge("spec_architect", "technical_spec_approval_gate")

    # Parallel fan-out: all three run in the same super-step, only once the
    # human has approved the technical spec.
    graph.add_conditional_edges(
        "technical_spec_approval_gate",
        routing.route_after_technical_spec_gate,
        {
            "seeder": "seeder",
            "backend_dev": "backend_dev",
            "frontend_dev": "frontend_dev",
            "spec_architect": "spec_architect",
        },
    )

    # Fan-in: integration_validator only runs once all three have completed.
    graph.add_edge("seeder", "integration_validator")
    graph.add_edge("backend_dev", "integration_validator")
    graph.add_edge("frontend_dev", "integration_validator")

    graph.add_conditional_edges(
        "integration_validator",
        routing.route_after_integration_validator,
        {"howto_helper": "howto_helper", "repair_router": "repair_router"},
    )

    # repair_router fans out to a variable subset of {seeder, backend_dev,
    # frontend_dev, integration_validator, end} — every possible
    # destination must appear in this mapping even though a given run only
    # takes one or two of them.
    graph.add_conditional_edges(
        "repair_router",
        routing.route_after_repair_router,
        {
            "seeder": "seeder",
            "backend_dev": "backend_dev",
            "frontend_dev": "frontend_dev",
            "integration_validator": "integration_validator",
            "end": END,
        },
    )

    graph.add_edge("howto_helper", END)

    return graph.compile(checkpointer=checkpointer)
