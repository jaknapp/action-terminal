import fcntl
import signal
from unittest.mock import patch

import pytest

from action.app.terminal import Terminal, start_terminal
from action.app.timer import Timer


class TestTerminal:
    @pytest.fixture
    def terminal(self):
        pid = 1234
        terminal_fd = 10
        return Terminal(pid, terminal_fd)

    @pytest.fixture
    def mock_timer_start(self, mocker):
        return mocker.patch.object(Timer, "start")

    @pytest.fixture
    def mock_timer_elapsed(self, mocker):
        return mocker.patch.object(Timer, "elapsed", return_value=7)

    def test_init(self, terminal):
        assert terminal.pid == 1234
        assert terminal._terminal_fd == 10

    @patch("os.write")
    def test_send_input(self, mock_write, terminal):
        input_data = "ls\n"
        terminal.send_input(input_data)
        mock_write.assert_called_once_with(terminal._terminal_fd, input_data.encode())

    @patch("os.getpgid", return_value=1234)
    @patch("os.killpg")
    def test_send_signal_with_str(self, mock_killpg, mock_getpgid, terminal):
        terminal.send_signal("SIGINT")
        mock_killpg.assert_called_once_with(1234, signal.SIGINT)

    @patch("os.getpgid", return_value=1234)
    @patch("os.killpg")
    def test_send_signal_with_int(self, mock_killpg, mock_getpgid, terminal):
        terminal.send_signal(signal.SIGTERM)
        mock_killpg.assert_called_once_with(1234, signal.SIGTERM)

    def test_send_signal_getpgid_error(self, terminal):
        """Test handling of exceptions when os.getpgid fails."""
        with (
            patch("os.getpgid", side_effect=Exception("getpgid failed")),
            pytest.raises(Exception) as excinfo,
        ):
            terminal.send_signal(15)  # Use any signal number or name
        assert "getpgid failed" in str(excinfo.value)

    def test_send_signal_killpg_error(self, terminal):
        """Test handling of exceptions when os.killpg fails."""
        with (
            patch("os.getpgid", return_value=1234),
            patch("os.killpg", side_effect=Exception("killpg failed")),
            pytest.raises(Exception) as excinfo,
        ):
            terminal.send_signal(15)  # Use any signal number or name
        assert "killpg failed" in str(excinfo.value)

    @patch("select.select")
    @patch("os.read")
    def test_read_output(
        self, mock_read, mock_select, terminal, mock_timer_start, mock_timer_elapsed
    ):
        outputs = [b"Hello World\n", b"(.venv) %n@%m %1~ %# "]
        mock_read.side_effect = outputs
        mock_select.return_value = ([terminal._terminal_fd], [], [])

        result = terminal.read()

        assert result.is_done is False
        assert result.output == "Hello World\n"
        assert result.error is None

        result = terminal.read()

        assert result.is_done
        assert result.output == "(.venv) %n@%m %1~ %# "
        assert result.error is None

    def test_read_with_stop_mark_found(self, terminal):
        """Test the read method when a stop mark is found in the output."""
        output_before_stop_mark = "command output before stop"
        stop_mark = "STOP_HERE"
        output_after_stop_mark = " command output after stop"

        with (
            patch("select.select", return_value=([terminal._terminal_fd], [], [])),
            patch(
                "os.read",
                side_effect=[
                    output_before_stop_mark.encode()
                    + stop_mark.encode()
                    + output_after_stop_mark.encode()
                    + stop_mark.encode()[:4],
                    stop_mark.encode()[4:],
                    b"",  # Simulate no more data
                ],
            ),
        ):
            result = terminal.read(stop_mark=stop_mark)

            expected_output = output_before_stop_mark + stop_mark
            assert result.output == expected_output
            assert terminal._read_buffer == output_after_stop_mark + stop_mark[:4]
            assert result.is_done is False
            assert result.stop_mark_found is True

            result = terminal.read(stop_mark=stop_mark)
            assert result.output == output_after_stop_mark + stop_mark[:4]
            assert terminal._read_buffer == ""
            assert result.is_done is False
            assert result.stop_mark_found is False

            result = terminal.read(stop_mark=stop_mark)
            assert result.output == stop_mark[4:]
            assert terminal._read_buffer == ""
            assert result.is_done is False
            assert result.stop_mark_found is True

    def test_read_with_prompt_and_extra_output(self, terminal):
        """
        Test the read method when the prompt is found but there is more
        output. The extra output should also be returned and the done
        flag should be True.
        """
        with (
            patch("select.select", return_value=([terminal._terminal_fd], [], [])),
            patch(
                "os.read",
                side_effect=[
                    b"some output (.venv) %n@%m %1~ %# and some more output",
                    b"",  # Simulate no more data
                ],
            ),
        ):
            result = terminal.read()

        assert result.output == "some output (.venv) %n@%m %1~ %# and some more output"
        assert result.is_done is True

    @patch("select.select")
    @patch("os.read", side_effect=OSError("mock os error"))
    def test_read_with_os_error(
        self, mock_read, mock_select, terminal, mock_timer_start, mock_timer_elapsed
    ):
        mock_select.return_value = ([terminal._terminal_fd], [], [])

        result = terminal.read()

        assert not result.is_done
        assert result.output == ""
        assert result.error == "mock os error"

    # TODO this test is too brittle because it has to mock the perf_counter which could
    # be called many times for different reasons.
    def test_read_timeout_handling(self, terminal: Terminal):
        """
        Test proper timeout handling if not finished reading before timeout,
        and verify select call with timeout.

        For time.perf_counter
        0 - timer.start()
        0 - time.perf_counter() >= timeout_perf_counter
        0 - idle_timer.elapsed() >= idle_timeout
        0 - idle_timeout - idle_timer.elapsed()
        0 - timeout_perf_counter - time.perf_counter()
        [read content]
        1 - idle_timer.start()
        1 - time.perf_counter() >= timeout_perf_counter
        1 - idle_timer.elapsed() >= idle_timeout
        1 - idle_timeout - idle_timer.elapsed()
        1 - timeout_perf_counter - time.perf_counter()
        [nothing to read]
        9 - time.perf_counter() >= timeout_perf_counter
        [end]
        """
        partial_output = "partial command output without stop mark or prompt"
        with (
            patch("select.select") as mock_select,
            patch("time.perf_counter", side_effect=[0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 9]),
            patch("os.read", return_value=partial_output.encode()),
        ):
            mock_select.side_effect = [([terminal._terminal_fd], [], []), ([], [], [])]
            result = terminal.read(
                stop_mark=None, timeout_perf_counter=8, idle_timeout=4
            )

        assert result.output == partial_output
        assert not result.is_done
        assert result.error is None


class TestStartTerminal:
    @pytest.fixture
    def mock_pty(self, mocker):
        return mocker.patch("pty.openpty", return_value=(3, 4))  # master_fd, slave_fd

    @pytest.fixture
    def mock_fork(self, mocker):
        return mocker.patch("os.fork", return_value=12345)

    @pytest.fixture
    def mock_os(self, mocker):
        return {
            "setsid": mocker.patch("os.setsid"),
            "dup2": mocker.patch("os.dup2"),
            "execv": mocker.patch("os.execv"),
            "close": mocker.patch("os.close"),
            "fcntl": mocker.patch("fcntl.fcntl"),
        }

    def test_start_terminal_parent_process(self, mock_pty, mock_fork, mock_os, mocker):
        # Mock fork to simulate the parent process
        mock_fork.return_value = 12345  # PID of the child process
        terminal = start_terminal()

        assert isinstance(terminal, Terminal)
        assert terminal.pid == 12345
        assert terminal._terminal_fd == 3
        mock_os["close"].assert_called_once_with(4)
        mock_os["fcntl"].assert_any_call(3, fcntl.F_GETFL)
        mock_os["fcntl"].assert_called_with(3, fcntl.F_SETFL, mocker.ANY)
        mock_os["execv"].assert_not_called()

    def test_start_terminal_child_process(self, mock_pty, mock_fork, mock_os, mocker):
        # Mock fork to simulate the child process
        mock_fork.return_value = 0
        mock_os["execv"].side_effect = SystemExit("Simulated exit due to execv")
        with pytest.raises(SystemExit):  # Assuming os.execv does not return
            start_terminal()

        mock_os["setsid"].assert_called_once()
        mock_os["dup2"].assert_has_calls(
            [mocker.call(4, 0), mocker.call(4, 1), mocker.call(4, 2)]
        )
        mock_os["execv"].assert_called_once_with("/bin/bash", ["bash"])
        mock_os["close"].assert_not_called()
        mock_os["fcntl"].assert_not_called()
