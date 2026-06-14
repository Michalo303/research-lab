from research_lab.orchestration.schemas import OrchestrationDecision

__all__ = ["OrchestrationDecision", "orchestrate_research_step"]


def __getattr__(name: str):
    if name == "orchestrate_research_step":
        from research_lab.orchestration.orchestrator import orchestrate_research_step

        return orchestrate_research_step
    raise AttributeError(name)
