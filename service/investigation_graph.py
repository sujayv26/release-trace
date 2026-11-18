"""Bounded decisions for the saved-fixture investigation."""

from collections.abc import Callable
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

from demo.release_trace_demo import INTERVENTIONS, Config, controlled_experiment

RESTORE_RETRIEVAL = "restore_retrieval"
MAX_EXPERIMENTS = 1


@dataclass(frozen=True)
class CaseEvidence:
    available_interventions: tuple[str, ...]
    stable_outcome: str
    candidate_outcome: str


def scripted_planner(evidence: CaseEvidence) -> str:
    if len(evidence.available_interventions) == 1 and evidence.candidate_outcome == "fail":
        return evidence.available_interventions[0]
    return "finish"


@dataclass(frozen=True)
class InvestigationContext:
    case: dict
    stable: Config
    candidate: Config
    planner: Callable[[CaseEvidence], str]
    experiment_budget: int


class State(TypedDict):
    report: dict
    intervention: str | None


def choose_experiment(state: State, runtime: Runtime[InvestigationContext]):
    context = runtime.context
    report = state["report"]
    available = tuple(name for name, setting in INTERVENTIONS.items()
                      if getattr(context.stable, setting) != getattr(context.candidate, setting))
    evidence = CaseEvidence(available, report["baseline"]["outcome"], report["candidate"]["outcome"])
    if context.experiment_budget == 0:
        reason = "experiment_budget_exhausted"
    elif not available:
        reason = "settings_match"
    else:
        try:
            choice = context.planner(evidence)
        except TimeoutError:
            reason = "planner_timeout"
        else:
            if choice in available:
                return {"intervention": choice}
            reason = "planner_abstained" if choice == "finish" else "unsupported_intervention"
    return {"report": {**report, "reason": reason}}


def rerun_case(state: State, runtime: Runtime[InvestigationContext]):
    context = runtime.context
    report = state["report"]
    name = state["intervention"]
    setting = INTERVENTIONS[name]
    experiment = controlled_experiment(context.case, context.stable, context.candidate, {}, name)
    supported = report["baseline"]["outcome"] == "pass" and experiment["outcome"] == "pass"
    return {"report": {
        **report, "experiment": experiment,
        "changed_parameter": {"name": setting, "before": getattr(context.candidate, setting), "after": getattr(context.stable, setting)},
        "outcome": "supported" if supported else "inconclusive",
        "reason": "restored_expected_citation" if supported else "rerun_did_not_establish_cause",
    }}


graph = (
    StateGraph(State, context_schema=InvestigationContext)
    .add_node("choose_experiment", choose_experiment)
    .add_node("rerun_case", rerun_case)
    .add_edge(START, "choose_experiment")
    .add_conditional_edges("choose_experiment", lambda state: "rerun" if state["intervention"] else "finish",
                           {"rerun": "rerun_case", "finish": END})
    .add_edge("rerun_case", END)
    .compile()
)


def run_graph(report, context: InvestigationContext):
    budget = context.experiment_budget
    if type(budget) is not int or not 0 <= budget <= MAX_EXPERIMENTS:
        raise ValueError("Invalid investigation experiment budget")
    return graph.invoke({"report": report, "intervention": None},
                        config={"recursion_limit": 5}, context=context)["report"]
