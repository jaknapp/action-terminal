from pydantic import BaseModel


class TerminalOutput(BaseModel):
    is_done: bool
    is_idle: bool
    stop_mark_found: bool
    timed_out: bool
    output: str
    error: str | None
