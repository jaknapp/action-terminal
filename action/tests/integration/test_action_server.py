import asyncio
import json
from typing import AsyncGenerator, Awaitable, Callable

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient

from action.app.action_composer import ActionComposer
from action.app.test_utils import assert_dicts_json_equal, assert_login_messages


@pytest.fixture(scope="session", autouse=True)
def set_event_loop_policy():
    policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(policy)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_client(
    aiohttp_client: Callable[[web.Application], Awaitable[TestClient]]
) -> AsyncGenerator[TestClient, None]:
    action_composer = ActionComposer()
    action_server, app = action_composer.compose_action_server_web_app()
    client = await aiohttp_client(app)
    yield client
    action_server.shutdown()


@pytest.mark.asyncio
class TestActionServer:
    async def test_websocket(self, setup_test_client: TestClient):
        """Test opening and closing a websocket."""
        session_id = "session-1"
        # Here we use the client directly provided by the fixture
        web_socket = await setup_test_client.ws_connect(
            "/websocket", headers={"session_id": session_id}
        )
        await web_socket.close()

    async def test_execute_loopback(self, setup_test_client: TestClient):
        """Test executing a command."""
        session_id = "session-1"
        loopback_payload = "test-command"
        request_data = {
            "session": {"session_id": session_id},
            "loopback_payload": loopback_payload,
        }
        response = await setup_test_client.post("/execute", json=request_data)
        assert response.status == 200

    async def test_error_handling(self, setup_test_client: TestClient):
        """Test the error middleware."""
        response = await setup_test_client.post("/execute", data="Invalid data")
        assert response.status == 500
        text = await response.text()
        data = json.loads(text)
        assert "error" in data and data["error"] == "Internal Server Error"

    async def test_separate_sessions(self, setup_test_client: TestClient):
        session_id_1 = "session-1"
        session_id_2 = "session-2"
        web_socket_1 = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id_1)
        )
        web_socket_2 = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id_2)
        )
        await setup_test_client.post(
            "/execute",
            json={
                "session": {"session_id": session_id_1},
                "loopback_payload": "payload 1",
            },
        )
        await setup_test_client.post(
            "/execute",
            json={
                "session": {"session_id": session_id_2},
                "loopback_payload": "payload 2",
            },
        )
        message_1 = json.loads(await web_socket_1.receive_json())
        message_2 = json.loads(await web_socket_2.receive_json())
        assert message_1["loopback_payload"] == "payload 1"
        assert message_2["loopback_payload"] == "payload 2"
        await web_socket_1.close()
        await web_socket_2.close()

    async def test_multiple_sockets_single_session(self, setup_test_client: TestClient):
        session_id = "session-1"
        web_sockets = [
            await setup_test_client.ws_connect(
                "/websocket", headers=dict(session_id=session_id)
            )
            for _ in range(3)
        ]
        await setup_test_client.post(
            "/execute",
            json={
                "session": {"session_id": session_id},
                "loopback_payload": "some payload",
            },
        )
        for web_socket in web_sockets:
            message = json.loads(await web_socket.receive_json())
            assert message["loopback_payload"] == "some payload"
            await web_socket.close()

    async def test_execute_new_processes_mocked(self, setup_test_client: TestClient):
        session_id = "session-1"
        web_socket = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id)
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                new_processes=[
                    dict(
                        mock_pid="12345",
                        mock_login_message=(
                            "Last login: Tue Apr 30 15:07:20 on ttys005\n>"
                        ),
                    )
                ],
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        assert_dicts_json_equal(
            dict(
                new_processes=[dict(pid="12345")],
                processes={
                    "12345": dict(
                        login_message="Last login: Tue Apr 30 15:07:20 on ttys005\n>"
                    )
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        await web_socket.close()

    async def test_execute_new_process(self, setup_test_client: TestClient):
        session_id = "session-1"
        web_socket = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id)
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                new_processes=[dict()],
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        assert_login_messages(response_dict)
        pid = response_dict["new_processes"][0]["pid"]
        assert_dicts_json_equal(
            dict(
                new_processes=[dict(pid=pid)],
                processes={pid: dict(login_message="")},
            ),
            response_dict,
            "Response data unexpected",
        )
        await web_socket.close()

    async def test_execute_multiple_new_processes(self, setup_test_client: TestClient):
        session_id = "session-1"
        web_socket = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id)
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                new_processes=[dict(), dict()],
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        assert_login_messages(response_dict)
        pid1 = response_dict["new_processes"][0]["pid"]
        pid2 = response_dict["new_processes"][1]["pid"]
        assert_dicts_json_equal(
            dict(
                new_processes=[dict(pid=pid1), dict(pid=pid2)],
                processes={
                    pid1: dict(login_message=""),
                    pid2: dict(login_message=""),
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        await web_socket.close()

    async def test_execute_new_process_new_commands(
        self, setup_test_client: TestClient
    ):
        session_id = "session-1"
        web_socket = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id)
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                new_processes=[
                    dict(
                        new_commands=[
                            dict(input='echo "Hello"\n'),
                            dict(input='echo "world"\n'),
                        ]
                    )
                ],
                poll_interval=5,
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        assert_login_messages(response_dict)
        pid1 = response_dict["new_processes"][0]["pid"]
        # Strip command prompt at the end
        response_dict["processes"][pid1]["new_commands"][0]["output"] = response_dict[
            "processes"
        ][pid1]["new_commands"][0]["output"][: len('echo "Hello"\nHello\n')]
        response_dict["processes"][pid1]["new_commands"][1]["output"] = response_dict[
            "processes"
        ][pid1]["new_commands"][1]["output"][: len('echo "world"\nworld\n')]
        assert_dicts_json_equal(
            dict(
                new_processes=[dict(pid=pid1)],
                processes={
                    pid1: dict(
                        login_message="",
                        new_commands=[
                            dict(output='echo "Hello"\nHello\n'),
                            dict(output='echo "world"\nworld\n'),
                        ],
                    ),
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        await web_socket.close()

    async def test_execute_sleep_and_interrupt(self, setup_test_client: TestClient):
        session_id = "session-1"
        web_socket = await setup_test_client.ws_connect(
            "/websocket", headers=dict(session_id=session_id)
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                new_processes=[dict(new_commands=[dict(input="sleep 500\n")])],
                poll_interval=0,
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        pid1 = response_dict["new_processes"][0]["pid"]
        new_command_id = response_dict["processes"][pid1]["new_commands"][0]["id"]
        assert_dicts_json_equal(
            dict(
                new_processes=[dict(pid=pid1)],
                processes={
                    pid1: dict(
                        # login_message="",
                        is_done_logging_in=False,
                        new_commands=[
                            dict(id=new_command_id)
                            # dict(id=new_command_id, done=False, output="sleep 500\n")
                        ],
                    ),
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        response_dict = json.loads(await web_socket.receive_json())
        assert_login_messages(response_dict)
        assert_dicts_json_equal(
            dict(
                processes={
                    pid1: dict(
                        login_message="",
                        commands={
                            new_command_id: dict(
                                done=False,
                                output="sleep 500\n",
                            )
                        },
                    ),
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                processes={
                    pid1: dict(
                        commands={
                            new_command_id: dict(
                                input="\x03",
                                signal="SIGINT",
                            )
                        }
                    )
                },
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        # Strip command prompt at the end
        response_dict["processes"][pid1]["commands"][new_command_id]["output"] = (
            response_dict["processes"][pid1]["commands"][new_command_id]["output"][
                : len("^C\n\n")
            ]
        )
        assert_dicts_json_equal(
            dict(
                processes={
                    pid1: dict(commands={new_command_id: dict(output="^C\n\n")}),
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        response = await setup_test_client.post(
            "/execute",
            json=dict(
                session=dict(session_id=session_id),
                processes={pid1: dict(new_commands=[dict(input='echo "Hello"\n')])},
            ),
        )
        assert response.status == 200
        response_dict = json.loads(await web_socket.receive_json())
        # Strip command prompt at the end
        response_dict["processes"][pid1]["new_commands"][0]["output"] = response_dict[
            "processes"
        ][pid1]["new_commands"][0]["output"][: len('echo "Hello"\nHello\n')]
        assert_dicts_json_equal(
            dict(
                processes={
                    pid1: dict(new_commands=[dict(output='echo "Hello"\nHello\n')]),
                },
            ),
            response_dict,
            "Response data unexpected",
        )
        await web_socket.close()


if __name__ == "__main__":
    # asyncio.run(pytest.main())
    pytest.main()
