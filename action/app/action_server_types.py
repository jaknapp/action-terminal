from pydantic import BaseModel

from action.app.action_service_types import (
    ActionServiceExecutionRequestNewProcess,
    ActionServiceExecutionRequestProcess,
    ActionServiceExecutionResponseNewProcess,
    ActionServiceExecutionResponseProcess,
)


class ActionServerResponse(BaseModel):
    error: str | None = None


class ActionServerSession(BaseModel):
    session_id: str


class ActionServerExecutionRequest(BaseModel):
    session: ActionServerSession
    loopback_payload: str | None = None
    new_processes: list[ActionServiceExecutionRequestNewProcess] | None = None
    processes: dict[str, ActionServiceExecutionRequestProcess] | None = None
    # The upper limit in seconds on how long to wait for a response before
    # returning the current results of all processes
    poll_interval: int | None = None


class ActionServerExecutionResponse(BaseModel):
    loopback_payload: str | None = None
    new_processes: list[ActionServiceExecutionResponseNewProcess | None] | None = None
    processes: dict[
        str, ActionServiceExecutionResponseProcess
    ] | None = None  # key is pid
    error: str | None = None
