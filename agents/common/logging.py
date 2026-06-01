"""Helpers de logging d'agents."""

def append_trace(state: dict, message: str) -> None:
    trace = state.get("agent_trace", [])
    trace.append(message)
    state["agent_trace"] = trace
