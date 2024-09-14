import asyncio
import os
import signal
import time
import traceback
import uuid
import weakref
from collections import defaultdict, deque
from typing import DefaultDict, Deque, Tuple

from action.app.action_service_types import (
    ActionServiceExecutionReference,
    ActionServiceExecutionRequest,
    ActionServiceExecutionRequestCommand,
    ActionServiceExecutionRequestNewCommand,
    ActionServiceExecutionResponse,
    ActionServiceExecutionResponseCommand,
    ActionServiceExecutionResponseNewCommand,
    ActionServiceExecutionResponseNewProcess,
    ActionServiceExecutionResponseProcess,
    ActionServiceObserver,
)
from action.app.terminal import Terminal, start_terminal


class ProcessExecution:
    def __init__(
        self,
        response_handle: list[ActionServiceExecutionResponse],
        new_process_index: int,
        has_data_event: asyncio.Event,
        ready_event: asyncio.Event,
        idle_timeout: int | None,
        execution_id: str,
        start_perf_counter: float,
        poll_interval: int,
    ):
        self._response_handle = response_handle
        self._new_process_index = new_process_index
        self._has_data_event = has_data_event
        self._ready_event = ready_event
        self._started = False
        self._finished_starting = False
        self._idle_timeout = idle_timeout
        self._execution_id = execution_id
        # The original perf counter for the purposes of the start of the process
        self._start_perf_counter = start_perf_counter
        self._poll_interval = poll_interval
        self._new_command_queue: asyncio.Queue[
            ActionServiceExecutionRequestNewCommand | None
        ] = asyncio.Queue()
        self._command_deque: Deque[
            Tuple[str, ActionServiceExecutionRequestNewCommand]
        ] = deque()
        self._terminal: Terminal | None = None
        self._pid: str | None = None
        # The start perf counter for the purposes of reading
        self._read_start_perf_counter: float | None = None
        self._running_command_id: str | None = None
        self._running_command_outputs: list[str] = []
        self._running_command_errors: list[str] = []
        self._running_command_is_new = False
        self._running_command_is_done = False
        self._read_task: asyncio.Task | None = None

    async def add_new_commands(
        self, new_commands: list[ActionServiceExecutionRequestNewCommand]
    ) -> None:
        for new_command in new_commands:
            await self._new_command_queue.put(new_command)

    def add_commands(
        self, commands: dict[str, ActionServiceExecutionRequestCommand]
    ) -> None:
        for command_id, command in commands.items():
            if command_id == self._running_command_id:
                if command.signal is not None:
                    self._print_log_prefix()
                    print(f"command_id={command_id}, sending signal {command.signal}")
                    self._terminal.send_signal(command.signal)
                if command.input is not None:
                    self._print_log_prefix()
                    print(f"command_id={command_id}, sending input {command.input}")
                    self._terminal.send_input(command.input)
            else:
                # TODO return as error in response
                self._print_log_prefix()
                print(
                    f"command_id={command_id}, "
                    f"dropping command that isn't running {command.signal} "
                    f"{command.input}"
                )

    async def wait_for_data(self) -> None:
        await self._has_data_event.wait()

    async def wait_for_ready(self) -> None:
        await self._ready_event.wait()

    def prepare_to_send_response(self) -> None:
        response = self._response_handle[0]

        # Handle new_processes response
        if (
            response.new_processes
            and response.new_processes[self._new_process_index] is None
        ):
            new_process_response = ActionServiceExecutionResponseNewProcess(
                pid=self._pid
            )
            response.new_processes[self._new_process_index] = new_process_response

        # Set up processes response
        process_response = self._get_process_response()
        self._output_running_command(process_response)

        # Handle new commands that didn't get to run
        while not self._new_command_queue.empty():
            new_command = self._new_command_queue.get_nowait()
            command_id = str(uuid.uuid4())
            self._command_deque.append((command_id, new_command))
            if process_response.new_commands is None:
                process_response.new_commands = []
            new_command_response = ActionServiceExecutionResponseNewCommand(
                id=command_id
            )
            process_response.new_commands.append(new_command_response)

        self._has_data_event.clear()
        self._ready_event.clear()

    def shutdown(self) -> None:
        os.kill(self._terminal.pid, signal.SIGTERM)
        self._command_deque.clear()
        self._new_command_queue.put_nowait(None)
        self._read_task.cancel()
        # Maybe drain command queue

    async def run(self):
        # Handle terminal start up
        if not self._started:
            self._started = True
            self._start()
            self._read_start_perf_counter = self._start_perf_counter
        # Read terminal start up
        self._running_command_is_done = False
        while not self._running_command_is_done:
            output, error, is_done = await self._read()
            if output is not None:
                self._running_command_outputs.append(output)
            if error is not None:
                self._running_command_errors.append(error)
            if output is not None or error is not None:
                self._has_data_event.set()
            self._running_command_is_done = is_done
        process_response = self._get_process_response()
        self._output_running_command(process_response)
        self._finished_starting = True
        # Handle commands
        while True:
            # Signify when all commands are done
            if not self._command_deque and self._new_command_queue.empty():
                self._ready_event.set()

            # Handle commands that were queued
            if self._command_deque:
                command_id, command = self._command_deque.popleft()
                self._running_command_is_new = False
                self._running_command_is_done = False
                self._print_log_prefix()
                print(f"Executing queued command {command_id}")
            # Handle new commands and exit sentinal command
            else:
                command = await self._new_command_queue.get()
                # Exit signal
                if command is None:
                    self._print_log_prefix()
                    print("Exiting")
                    break
                command_id = str(uuid.uuid4())
                self._running_command_is_new = True
                self._running_command_is_done = False
                self._print_log_prefix()
                print(f"Executing new command {command_id}")
            self._running_command_id = command_id
            if command.signal is not None:
                self._print_log_prefix()
                print(f"command_id={command_id}, sending signal {command.signal}")
                self._terminal.send_signal(command.signal)
            if command.input is not None:
                self._print_log_prefix()
                print(f"command_id={command_id}, sending input {command.input}")
                self._terminal.send_input(command.input)

            # Read command output
            while True:
                output, error, is_done = await self._read()
                if output is not None:
                    self._running_command_outputs.append(output)
                if error is not None:
                    self._running_command_errors.append(error)
                if output is not None or error is not None:
                    self._has_data_event.set()
                self._running_command_is_done = is_done
                if is_done:
                    self._print_log_prefix()
                    print(f"{command_id} is done")
                    break
            process_response = self._get_process_response()
            self._output_running_command(process_response)
            self._running_command_id = None

    def _print_log_prefix(self) -> None:
        print(
            f"{self._execution_id} {self._pid} "
            f"{time.perf_counter() - self._start_perf_counter:.2f} ",
            end="",
        )

    def _get_process_response(self) -> ActionServiceExecutionResponseProcess:
        response = self._response_handle[0]
        if response.processes is None:
            response.processes = {}
        if self._pid in response.processes:
            process_response = response.processes[self._pid]
        else:
            process_response = ActionServiceExecutionResponseProcess()
            response.processes[self._pid] = process_response
        return process_response

    def _output_running_command(
        self, process_response: ActionServiceExecutionResponseProcess
    ) -> None:
        if not self._finished_starting:
            output, error = self._get_and_clear_running_command_output_errors()
            process_response.login_message = output
            process_response.error = error
            process_response.is_done_logging_in = self._running_command_is_done
            return
        if self._running_command_id is None:
            return
        output, error = self._get_and_clear_running_command_output_errors()
        if self._running_command_is_new:
            if process_response.new_commands is None:
                process_response.new_commands = []
            new_command_response = ActionServiceExecutionResponseNewCommand(
                id=(
                    None if self._running_command_is_done else self._running_command_id
                ),
                done=self._running_command_is_done,
                output=output,
                error=error,
            )
            process_response.new_commands.append(new_command_response)
        else:
            response_command = ActionServiceExecutionResponseCommand(
                done=self._running_command_is_done,
                output=output,
                error=error,
            )
            if process_response.commands is None:
                process_response.commands = {}
            process_response.commands[self._running_command_id] = response_command
        self._running_command_is_new = False

    def _get_and_clear_running_command_output_errors(
        self,
    ) -> Tuple[str | None, str | None]:
        output = (
            "".join(self._running_command_outputs)
            if self._running_command_outputs
            else None
        )
        error = (
            "".join(self._running_command_errors)
            if self._running_command_errors
            else None
        )
        self._running_command_outputs.clear()
        self._running_command_errors.clear()
        return output, error

    def _start(self) -> None:
        self._terminal = start_terminal()
        self._pid = str(self._terminal.pid)
        new_process_response = ActionServiceExecutionResponseNewProcess(pid=self._pid)
        response = self._response_handle[0]
        response.new_processes[self._new_process_index] = new_process_response
        self._has_data_event.set()

    async def _read(self) -> Tuple[str, str, bool]:
        idle_timeout = 8.0 if self._idle_timeout is None else self._idle_timeout
        timeout_perf_counter = self._read_start_perf_counter + self._poll_interval
        self._print_log_prefix()
        print(
            "reading terminal "
            f"running_command_id={self._running_command_id}, "
            f"poll_interval={self._poll_interval}, "
            f"stop_mark={None}, "
            f"timeout_perf_counter={timeout_perf_counter}, "
            f"idle_timeout={idle_timeout}"
        )
        self._read_task = asyncio.create_task(
            asyncio.to_thread(
                self._terminal.read,
                stop_mark=None,
                timeout_perf_counter=timeout_perf_counter,
                idle_timeout=self._idle_timeout,
            )
        )
        terminal_output = await self._read_task
        self._print_log_prefix()
        print(
            "read terminal "
            f"running_command_id={self._running_command_id}, "
            f"poll_interval={self._poll_interval}, "
            f"is_done={terminal_output.is_done}, "
            f"timed_out={terminal_output.timed_out}, "
            f"is_idle={terminal_output.is_idle}, "
            f"stop_mark_found={terminal_output.stop_mark_found}, "
            f"output={terminal_output.output[:10]}, "
            f"len(output)={len(terminal_output.output)}, "
            f"error={terminal_output.error}",
        )
        self._read_start_perf_counter = min(time.perf_counter(), timeout_perf_counter)
        login_message = terminal_output.output
        error = terminal_output.error
        is_done = terminal_output.is_done
        return login_message, error, is_done


class ActionService:
    def __init__(self):
        self._observer: ActionServiceObserver = None
        self._process_execution_dict: dict[Tuple[str, int], ProcessExecution] = {}
        self._pid_to_process_execution_dict: dict[str, ProcessExecution] = {}
        self._execution_id_poll_interval_handle_dict: DefaultDict[
            str, list[int]
        ] = defaultdict(lambda: [0])
        self._execution_tasks_list: dict[str, list[dict[str, asyncio.Task]]] = {}
        self._shutting_down = False

    def set_observer(self, observer: ActionServiceObserver) -> None:
        self._observer = observer

    def set_weak_observer(self, observer: ActionServiceObserver) -> None:
        self.set_observer(weakref.ref(observer))

    def execute(
        self, request: ActionServiceExecutionRequest
    ) -> ActionServiceExecutionReference:
        start_perf_counter = time.perf_counter()
        execution_id = str(uuid.uuid4())
        print(
            f"{execution_id} {time.perf_counter() - start_perf_counter:.2f} "
            f"Creating execution"
        )
        reference = ActionServiceExecutionReference(execution_id=execution_id)
        asyncio.create_task(self._try_execute(reference, request, start_perf_counter))
        return reference

    def set_poll_interval(
        self, reference: ActionServiceExecutionReference, poll_interval: int
    ) -> None:
        # TODO we should cancel the existing waiting for ready tasks and
        # recreate them with the appropriate timeout.
        print(
            f"{reference.execution_id} "
            f"setting poll interval "
            f"{self._execution_id_poll_interval_handle_dict[reference.execution_id][0]}"
            f" -> {poll_interval}"
        )
        self._execution_id_poll_interval_handle_dict[reference.execution_id][
            0
        ] = poll_interval

    def shutdown(self) -> None:
        print(f"Shutting down processes {len(self._process_execution_dict)}")
        self._shutting_down = True
        for process in self._process_execution_dict.values():
            # TODO make public
            print(f"Shutting down process {process._pid}")
            process.shutdown()
        for tasks_list in self._execution_tasks_list.values():
            for task_dict in tasks_list:
                for task in task_dict.values():
                    task.cancel()

    async def _try_execute(
        self,
        reference: ActionServiceExecutionReference,
        request: ActionServiceExecutionRequest,
        start_perf_counter: float,
    ) -> None:
        try:
            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} "
                f"Starting execution"
            )
            execution_id = reference.execution_id
            response_handle = [
                ActionServiceExecutionResponse(execution_id=execution_id),
            ]
            try:
                await self._execute(
                    reference, request, start_perf_counter, response_handle
                )
            except Exception as e:
                print(
                    f"{reference.execution_id} "
                    f"{time.perf_counter() - start_perf_counter:.2f} "
                    f"Unhandled exception in _execute ",
                    str(e),
                )
                traceback.print_exc()
                response_handle[0].error = str(e)
                await self._observer.receive_execution_response(response_handle[0])
        except Exception as e:
            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} "
                f"Unhandled exception in receive_execution_response ",
                str(e),
            )
        print(
            f"{reference.execution_id} {time.perf_counter() - start_perf_counter:.2f} "
            f"Done executing"
        )

    async def _execute(
        self,
        reference: ActionServiceExecutionReference,
        request: ActionServiceExecutionRequest,
        start_perf_counter: float,
        response_handle: list[ActionServiceExecutionResponse],
    ) -> None:
        if self._observer is None:
            return

        async def send_response(response: ActionServiceExecutionResponse):
            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} ",
                end="",
            )
            if response.new_processes is not None:
                pids = ", ".join(
                    [new_process.pid for new_process in response.new_processes]
                )
                print(
                    f", new_processes={len(response.new_processes)}, pids=[{pids}]",
                    end="",
                )
            if response.processes is not None:
                new_commands_sum = sum(
                    len(process.new_commands)
                    for process in response.processes.values()
                    if process.new_commands is not None
                )
                commands_sum = sum(
                    len(process.commands)
                    for process in response.processes.values()
                    if process.commands is not None
                )
                new_commands_errors_sum = sum(
                    1
                    for process in response.processes.values()
                    if process.new_commands is not None
                    for new_command in process.new_commands
                    if new_command.error is not None
                )
                commands_errors_sum = sum(
                    1
                    for process in response.processes.values()
                    if process.commands is not None
                    for command in process.commands.values()
                    if command.error is not None
                )
                errors_total = new_commands_errors_sum + commands_errors_sum
                print(
                    f", new_commands={new_commands_sum}"
                    f", commands={commands_sum}"
                    f", errors={errors_total}",
                    end="",
                )
            print()
            await self._observer.receive_execution_response(response)
            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} "
                "Response sent",
            )

        poll_interval_handle = self._execution_id_poll_interval_handle_dict[
            reference.execution_id
        ]
        poll_interval_handle[0] = (
            10 if request.poll_interval is None else request.poll_interval
        )
        response = response_handle[0]
        # Request loopback bypasses real execution logic
        if request.loopback_payload is not None:
            response.loopback_payload = request.loopback_payload
            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} "
                f"Sending loopback payload {response.loopback_payload[:15]}"
            )
            await send_response(response=response_handle[0])
            return
        response.new_processes = (
            ([None] * len(request.new_processes)) if request.new_processes else None
        )
        # Handle mock responses
        need_to_send_mock_response = any(
            new_process_request.mock_pid is not None
            for new_process_request in (request.new_processes or [])
        )
        for i, new_process_request in enumerate(request.new_processes or []):
            if new_process_request.mock_pid is None:
                continue
            pid = new_process_request.mock_pid
            new_process_response = ActionServiceExecutionResponseNewProcess(pid=pid)
            response.new_processes[i] = new_process_response
            if response.processes is None:
                response.processes = {}
            response.processes[pid] = ActionServiceExecutionResponseProcess(
                login_message=new_process_request.mock_login_message
            )
        for pid, process_request in (request.processes or {}).items():
            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} "
                f"Adding {len(process_request.new_commands or [])} new commands and "
                f"{len(process_request.commands or {})} commands to process {pid} "
            )
            process = self._pid_to_process_execution_dict[pid]
            if process_request.new_commands:
                await process.add_new_commands(process_request.new_commands)
            if process_request.commands:
                process.add_commands(process_request.commands)
        processes: list[ProcessExecution] = []
        for i, new_process_request in enumerate(request.new_processes or []):
            if new_process_request.mock_pid is not None:
                continue
            process = ProcessExecution(
                response_handle=response_handle,
                new_process_index=i,
                has_data_event=asyncio.Event(),
                ready_event=asyncio.Event(),
                idle_timeout=new_process_request.idle_timeout,
                execution_id=reference.execution_id,
                start_perf_counter=start_perf_counter,
                # TODO pass the handle?
                poll_interval=10,  # poll_interval_handle[0],
            )
            if new_process_request.new_commands:
                print(
                    f"{reference.execution_id} "
                    f"{time.perf_counter() - start_perf_counter:.2f} "
                    f"Adding {len(new_process_request.new_commands)} new commands to "
                    f"new process with index {i}"
                )
                await process.add_new_commands(new_process_request.new_commands)
            processes.append(process)
            self._process_execution_dict[
                (reference.execution_id, id(process))
            ] = process
        process_tasks = [asyncio.create_task(process.run()) for process in processes]
        for process_task in process_tasks:
            key_tuple = (reference.execution_id, id(process))

            def remove_process_execution(task: asyncio.Task[None]) -> None:
                # TODO remove from running processes
                if key_tuple in self._process_execution_dict:
                    del self._process_execution_dict[key_tuple]

            process_task.add_done_callback(remove_process_execution)
        loop_start_perf_counter = start_perf_counter

        # TODO this might be overkill.
        pid_to_waiting_for_data_task = {}
        waiting_for_data_task_to_pid = {}
        pid_to_waiting_for_ready_task = {}
        waiting_for_ready_task_to_pid = {}
        self._execution_tasks_list[reference.execution_id] = [
            pid_to_waiting_for_data_task,
            pid_to_waiting_for_ready_task,
        ]
        while processes or need_to_send_mock_response:
            if processes:
                print(
                    f"{reference.execution_id} "
                    f"{time.perf_counter() - start_perf_counter:.2f} "
                    f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                    f"waiting for at least one processes to have data out of "
                    f"{len(processes)}"
                )
                # Create tasks to wait for at least one with some data
                for process in processes:
                    if process._pid in pid_to_waiting_for_data_task:
                        continue
                    task = asyncio.create_task(process.wait_for_data())
                    pid_to_waiting_for_data_task[process._pid] = task
                    waiting_for_data_task_to_pid[task] = process._pid

                done_tasks, pending_tasks = await asyncio.wait(
                    pid_to_waiting_for_data_task.values(),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                cancelled_task_count = sum(1 for task in done_tasks if task.cancelled())
                print(
                    f"{reference.execution_id} "
                    f"{time.perf_counter() - start_perf_counter:.2f} "
                    f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                    f"waiting for data processes done={len(done_tasks)}, "
                    f"cancelled={cancelled_task_count}, "
                    f"pending={len(pending_tasks)}"
                )

                for done_task in done_tasks:
                    pid = waiting_for_data_task_to_pid.pop(done_task)
                    del pid_to_waiting_for_data_task[pid]

                if cancelled_task_count == len(done_tasks) + len(pending_tasks):
                    print(
                        f"{reference.execution_id} "
                        f"{time.perf_counter() - start_perf_counter:.2f} "
                        f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                        "All tasks cancelled. Exiting"
                    )
                    return

                timeout = (
                    poll_interval_handle[0]
                    - loop_start_perf_counter
                    + time.perf_counter()
                )
                if timeout > 0:
                    print(
                        f"{reference.execution_id} "
                        f"{time.perf_counter() - start_perf_counter:.2f} "
                        f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                        f"waiting {timeout} of {poll_interval_handle[0]}s for processes"
                        f" {len(processes)}"
                    )

                    # Create tasks to wait for them all to be done or for the timeout,
                    # whichever comes first
                    for process in processes:
                        if process._pid in pid_to_waiting_for_ready_task:
                            continue
                        task = asyncio.create_task(process.wait_for_ready())
                        pid_to_waiting_for_ready_task[process._pid] = task
                        waiting_for_ready_task_to_pid[task] = process._pid

                    done_tasks, pending_tasks = await asyncio.wait(
                        pid_to_waiting_for_ready_task.values(),
                        timeout=timeout,
                        return_when=asyncio.ALL_COMPLETED,
                    )
                    cancelled_task_count = sum(
                        1 for task in done_tasks if task.cancelled()
                    )
                    print(
                        f"{reference.execution_id} "
                        f"{time.perf_counter() - start_perf_counter:.2f} "
                        f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                        f"waiting for done processes done={len(done_tasks)}, "
                        f"cancelled={cancelled_task_count}, "
                        f"pending={len(pending_tasks)}"
                    )
                    for done_task in done_tasks:
                        pid = waiting_for_ready_task_to_pid.pop(done_task)
                        del pid_to_waiting_for_ready_task[pid]

                    if cancelled_task_count == len(done_tasks) + len(pending_tasks):
                        print(
                            f"{reference.execution_id} "
                            f"{time.perf_counter() - start_perf_counter:.2f} "
                            f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                            "All tasks cancelled. Exiting"
                        )
                        return

            print(
                f"{reference.execution_id} "
                f"{time.perf_counter() - start_perf_counter:.2f} "
                f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                f"sending output"
            )
            for process in processes:
                if (
                    process._started
                    and process._pid not in self._pid_to_process_execution_dict
                ):
                    self._pid_to_process_execution_dict[process._pid] = process
                process.prepare_to_send_response()
            response = response_handle[0]
            response_handle[0] = ActionServiceExecutionResponse(
                execution_id=response.execution_id
            )
            await send_response(response)
            need_to_send_mock_response = False

            if all(process_task.done() for process_task in process_tasks):
                print(
                    f"{reference.execution_id} "
                    f"{time.perf_counter() - start_perf_counter:.2f} "
                    f"{time.perf_counter() - loop_start_perf_counter:.2f} "
                    "All processes completed."
                )
                break

            # Respond every poll_interval or sooner if all processes finished early
            loop_start_perf_counter = min(
                loop_start_perf_counter + poll_interval_handle[0], time.perf_counter()
            )
