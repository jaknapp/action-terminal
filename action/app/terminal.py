import fcntl
import os
import pty
import select
import signal
import time
from typing import Tuple

from action.app.terminal_types import TerminalOutput
from action.app.timer import Timer


class Terminal:
    def __init__(
        self,
        pid: int,
        terminal_fd: int,
    ):
        self.pid = pid
        self._terminal_fd = terminal_fd
        self._read_first_prompt = False
        self._read_buffer = ""
        self._done_buffer = ""

    def send_input(self, input: str):
        os.write(self._terminal_fd, input.encode())

    def send_signal(self, signal_param: int | str):
        if isinstance(signal_param, str):
            signal_int = getattr(signal, signal_param)
        else:
            signal_int = signal_param
        os.killpg(os.getpgid(self.pid), signal_int)

    def read(
        self,
        stop_mark: str | None = None,
        timeout_perf_counter: float | None = None,
        idle_timeout: float | None = None,
    ) -> TerminalOutput:
        """
        Read output from the master file descriptor of the pseudo-terminal.

        @param stop_mark read up until text matching stop_mark is read.
          Any remaining read text will be buffered.
        """
        timed_out = False
        is_idle = False
        is_done = False
        stop_mark_found = False
        error = None
        outputs = []
        prompts: list[str] = ["(.venv) %n@%m %1~ %# ", "\x1b[00m$ ", "cmd> "]
        max_prompt_length = max(map(len, prompts))
        stop_mark_length = 0 if stop_mark is None else len(stop_mark)
        done_buffer_size = max(max_prompt_length, stop_mark_length)
        idle_timer = Timer()
        idle_timer.start()
        while (
            not (
                timed_out := (
                    timeout_perf_counter is not None
                    and time.perf_counter() >= timeout_perf_counter
                )
            )
            and not (
                is_idle := (
                    idle_timeout is not None and idle_timer.elapsed() >= idle_timeout
                )
            )
            and not is_done
            and not stop_mark_found
        ):
            try:
                idle_timer.start()
                if self._read_buffer:
                    output_str = self._read_buffer
                    self._read_buffer = ""
                else:
                    _, _, _ = select.select(
                        [self._terminal_fd],  # rlist
                        [],  # wlist
                        [],  # xlist
                        None,  # timeout
                    )
                    output_bytes = os.read(self._terminal_fd, 1024)
                    output_str = output_bytes.decode("utf-8")
                # Code to handle detection of the stop mark and prompt. Detecting the
                # prompt signifies is_done=True. There may be many unhandled corner
                # cases here such as prompts and stop marks that overlap each other.
                done_buffer_output_index = len(self._done_buffer)
                self._done_buffer += output_str
                stop_index = (
                    -1 if stop_mark is None else self._done_buffer.find(stop_mark)
                )
                stop_mark_found = stop_index >= 0

                def find_prompt_index(buffer: str) -> Tuple[int, str]:
                    for prompt in prompts:
                        prompt_index = buffer.find(prompt)
                        if prompt_index >= 0:
                            return prompt_index, prompt
                    return -1, ""

                if stop_mark_found:
                    stop_mark_done_buffer_index = stop_index + stop_mark_length
                    output_str = self._done_buffer[
                        done_buffer_output_index:stop_mark_done_buffer_index
                    ]
                    self._read_buffer = self._done_buffer[stop_mark_done_buffer_index:]
                    prompt_index, prompt = find_prompt_index(
                        self._done_buffer[:stop_mark_done_buffer_index]
                    )
                else:
                    stop_mark_done_buffer_index = 0
                    prompt_index, prompt = find_prompt_index(self._done_buffer)
                is_done = prompt_index >= 0
                prompt_done_buffer_index = prompt_index + len(prompt) if is_done else 0
                new_done_buffer_index = max(
                    0,
                    len(self._done_buffer) - done_buffer_size,
                    stop_mark_done_buffer_index,
                    prompt_done_buffer_index,
                )
                self._done_buffer = self._done_buffer[new_done_buffer_index:]
                output = "".join(
                    (
                        f"\\x{ord(char):02x}"
                        if (ord(char) < 32 or ord(char) == 127)
                        and char not in ["\r", "\n"]
                        else char
                    )
                    for char in output_str
                )
                outputs.append(output)
                break
            except OSError as e:
                print(f"{self.pid} Terminal os error: {e}")
                error = str(e)
                break
        return TerminalOutput(
            is_done=is_done,
            is_idle=is_idle,
            stop_mark_found=stop_mark_found,
            timed_out=timed_out,
            output="".join(outputs).replace("\r\n", "\n").replace("\r", "\n"),
            error=error,
        )


def start_terminal() -> Terminal:
    master, slave = pty.openpty()
    pid = os.fork()
    if pid == 0:
        os.setsid()
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        # Does not return
        os.execv("/bin/bash", ["bash"])
    os.close(slave)
    # Not sure this is needed still
    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    return Terminal(
        pid=pid,
        terminal_fd=master,
    )
