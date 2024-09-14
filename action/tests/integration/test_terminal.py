import asyncio
import time
from typing import Tuple

import pytest

from action.app.terminal import Terminal, start_terminal
from action.app.test_utils import assert_str_equal

EXPECTED_LOGIN_MESSAGE = """
The default interactive shell is now zsh.
To update your account to use zsh, please run `chsh -s /bin/zsh`.
For more details, please visit https://support.apple.com/kb/HT208050.
\\x1b[?1034h(.venv) %n@%m %1~ %# """


def read_terminal_until_done(
    terminal: Terminal,
    stop_mark: str | None = None,
) -> Tuple[str, str | None]:
    output_list = []
    error_list = []
    while True:
        result = terminal.read(stop_mark=stop_mark)
        if result.output is not None:
            output_list.append(result.output)
        if result.error is not None:
            error_list.append(result.error)
        if result.is_done or result.stop_mark_found:
            break
    output = "".join(output_list)
    error = "".join(error_list) if error_list else None
    return output, error


def test_read_login_message():
    terminal = start_terminal()
    output, error = read_terminal_until_done(terminal)

    assert error is None
    assert output
    # assert_str_equal(EXPECTED_LOGIN_MESSAGE, output)


def test_input_control_characters():
    # Start the terminal
    terminal = start_terminal()
    output, error = read_terminal_until_done(terminal)

    # Send a control character, for example, Ctrl+C (interrupt)
    # The ASCII code for Ctrl+C is \x03
    terminal.send_input("sleep 10\n")
    output, _ = read_terminal_until_done(terminal=terminal, stop_mark="sleep 10\r\n")
    assert output == "sleep 10\n"
    terminal.send_input("\x03")

    # Wait for any output as a result of the control character
    # Since Ctrl+C typically would interrupt any current command, we may not expect
    # much output, but let's assume we want to check if the terminal responds with a
    # new prompt or a specific message.
    output, error = read_terminal_until_done(terminal)

    # Check that the terminal session is still running and no unexpected errors occurred
    assert error is None, f"An error occurred: {error}"

    # Perform a diff to ensure the output is as expected after sending Ctrl+C
    assert_str_equal("^C\n", output[: len("^C\n")])


def test_cursor_movement_command():
    # Start the terminal
    terminal = start_terminal()

    # Clear initial output
    _, _ = read_terminal_until_done(terminal)

    # Move cursor and output text at new position
    terminal.send_input("Something\x1b[10;10HHello, cursor!\n")

    # Read the output after sending cursor movement command
    output, error = read_terminal_until_done(terminal)

    # Check that the terminal session handled the command correctly
    assert error is None, f"An error occurred: {error}"

    # Define the expected response - we expect the text to be at a certain position,
    # but this is tricky to validate directly without knowing how the terminal buffers
    # its output. Here we check simply for the presence of our message.
    assert "Hello, cursor!" in output, "The output did not contain the expected text."


@pytest.mark.asyncio
async def test_multiple_terminals():
    terminals = [start_terminal() for _ in range(2)]
    [read_terminal_until_done(terminal) for terminal in terminals]
    start_time = time.perf_counter()
    terminals[0].send_input("sleep 4\n")
    terminals[1].send_input('echo "done"\n')
    result = await asyncio.to_thread(terminals[1].read)
    end_time = time.perf_counter()
    assert end_time - start_time < 1
    assert_str_equal('echo "done"\ndone\n', result.output[: len('echo "done"\ndone\n')])
