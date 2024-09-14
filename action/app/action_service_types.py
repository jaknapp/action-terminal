from abc import ABC, abstractmethod

from pydantic import BaseModel


class ActionServiceExecutionRequestNewCommand(BaseModel):
    input: str | None = None
    signal: str | None = None


class ActionServiceExecutionRequestNewProcess(BaseModel):
    mock_pid: str | None = None
    mock_login_message: str | None = None
    idle_timeout: int | None = None
    new_commands: list[ActionServiceExecutionRequestNewCommand] | None = None


class ActionServiceExecutionRequestCommand(BaseModel):
    input: str | None = None
    signal: str | None = None


class ActionServiceExecutionRequestProcess(BaseModel):
    new_commands: list[ActionServiceExecutionRequestNewCommand] | None = None
    commands: dict[str, ActionServiceExecutionRequestCommand] | None = None


class ActionServiceExecutionRequest(BaseModel):
    loopback_payload: str | None = None
    new_processes: list[ActionServiceExecutionRequestNewProcess] | None = None
    processes: dict[str, ActionServiceExecutionRequestProcess] | None = None
    # The upper limit in seconds on how long to wait for a response before
    # returning the current results of all processes
    poll_interval: int | None = None


class ActionServiceExecutionReference(BaseModel):
    execution_id: str


class ActionServiceExecutionResponseNewProcess(BaseModel):
    pid: str


class ActionServiceExecutionResponseNewCommand(BaseModel):
    id: str | None = None
    done: bool = True
    exit_code: int | None = None
    output: str | None = None
    error: str | None = None


class ActionServiceExecutionResponseCommand(BaseModel):
    done: bool = True
    exit_code: int | None = None
    output: str | None = None
    error: str | None = None


class ActionServiceExecutionResponseProcess(BaseModel):
    login_message: str | None = None
    error: str | None = None
    is_done_logging_in: bool = True
    new_commands: list[ActionServiceExecutionResponseNewCommand] | None = None
    commands: dict[str, ActionServiceExecutionResponseCommand] | None = None


class ActionServiceExecutionResponse(BaseModel):
    execution_id: str
    loopback_payload: str | None = None
    new_processes: list[ActionServiceExecutionResponseNewProcess | None] | None = None
    processes: dict[
        str, ActionServiceExecutionResponseProcess
    ] | None = None  # key is pid
    error: str | None = None


class ActionServiceObserver(ABC):
    @abstractmethod
    async def receive_execution_response(
        self, response: ActionServiceExecutionResponse
    ) -> None:
        pass
