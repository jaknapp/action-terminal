from collections import defaultdict
from typing import Awaitable, Callable, DefaultDict, Tuple

from aiohttp import web

from action.app.action_server_types import (
    ActionServerExecutionRequest,
    ActionServerExecutionResponse,
    ActionServerResponse,
)
from action.app.action_service import ActionService, ActionServiceObserver
from action.app.action_service_types import (
    ActionServiceExecutionReference,
    ActionServiceExecutionRequest,
    ActionServiceExecutionResponse,
)


class ActionServerExecutionObserver(ActionServiceObserver):
    def __init__(
        self,
        session_id_web_sockets: DefaultDict[str, list[web.WebSocketResponse]],
        execution_id_session_id_dict: dict[str, str],
    ):
        self._session_id_web_sockets = session_id_web_sockets
        self._execution_id_session_id_dict = execution_id_session_id_dict

    async def receive_execution_response(
        self, response: ActionServiceExecutionResponse
    ) -> None:
        if (
            session_id := self._execution_id_session_id_dict.get(response.execution_id)
        ) is None:
            print(
                f"Session not found for {response.execution_id}. Dropping response "
                f"{response}"
            )
            return
        web_sockets = self._session_id_web_sockets[session_id]
        print(
            f"Sending execution ({response.execution_id}) response for session "
            f"{session_id} to {len(web_sockets)} web sockets."
        )
        for web_socket in web_sockets:
            server_response = ActionServerExecutionResponse(
                loopback_payload=response.loopback_payload,
                new_processes=response.new_processes,
                processes=response.processes,
                error=response.error,
            )
            await web_socket.send_json(
                server_response.model_dump_json(exclude_defaults=True)
            )


def _get_request_log_values(
    request: web.Request,
) -> Tuple[str | None, str | None, str | None, str | None, str | None, str | None]:
    websocket_key = request.headers.get("Sec-WebSocket-Key")
    peername = request.transport.get_extra_info("peername")
    host, port = (None, None) if peername is None else peername[:2]
    host = None if host is None else str(host)
    port = None if port is None else str(port)
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    remote = request.remote
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(",")[
            0
        ]  # In case there are multiple addresses
    else:
        client_ip = request.remote
    return client_ip, websocket_key, host, port, remote, x_forwarded_for


class ActionServer:
    """Handle web requests and invoke the ActionService"""

    def __init__(self, action_service: ActionService):
        self._action_service = action_service
        self._session_id_web_sockets: DefaultDict[
            str, list[web.WebSocketResponse]
        ] = defaultdict(list)
        self._session_id_executions_dict: DefaultDict[
            str, list[ActionServiceExecutionReference]
        ] = defaultdict(list)
        self._execution_id_session_id_dict: dict[str, str] = {}
        observer = ActionServerExecutionObserver(
            session_id_web_sockets=self._session_id_web_sockets,
            execution_id_session_id_dict=self._execution_id_session_id_dict,
        )
        self._action_service.set_observer(observer)

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        session_id = request.headers.get("session_id")

        # This is all not strictly necessary but am printing it for curiosity
        request_log_values = _get_request_log_values(request)
        print(f"WebSocket connection starting {request_log_values}")
        if session_id is None:
            server_response = ActionServerResponse(
                error="Missing 'session_id' header in websocket request.",
                status=400,
            )
            return web.json_response(
                server_response.model_dump_json(exclude_defaults=True)
            )
        web_socket = web.WebSocketResponse()
        await web_socket.prepare(request)
        self._session_id_web_sockets[session_id].append(web_socket)

        async for message in web_socket:
            if message.type == web.WSMsgType.TEXT:
                print(f"Unexpected message: {message.data}")
            elif message.type == web.WSMsgType.ERROR:
                print(
                    "WebSocket connection closed with exception:",
                    web_socket.exception(),
                )

        print(f"WebSocket connection closed {request_log_values}")
        return web_socket

    async def execute(self, request: web.Request) -> web.Response:
        request_log_values = _get_request_log_values(request)
        print(f"Execute POST request for {request_log_values}")
        print(await request.json())
        server_request = ActionServerExecutionRequest(**await request.json())
        print(server_request.model_dump_json(exclude_defaults=True))
        service_request = ActionServiceExecutionRequest(
            loopback_payload=server_request.loopback_payload,
            new_processes=server_request.new_processes,
            processes=server_request.processes,
            poll_interval=server_request.poll_interval,
        )
        session_id = server_request.session.session_id
        # Update poll interval for all executions in this session
        if server_request.poll_interval is not None:
            session_executions = self._session_id_executions_dict.get(session_id, [])
            for session_execution in session_executions:
                self._action_service.set_poll_interval(
                    reference=session_execution,
                    poll_interval=server_request.poll_interval,
                )
        # Start a new execution
        execution_reference = self._action_service.execute(service_request)
        self._session_id_executions_dict[session_id].append(execution_reference)
        self._execution_id_session_id_dict[
            execution_reference.execution_id
        ] = session_id
        print(
            f"Execution reference {execution_reference.execution_id} for session "
            f"{session_id}"
        )
        server_response = ActionServerResponse()
        return web.json_response(server_response.model_dump_json(exclude_defaults=True))

    def shutdown(self) -> None:
        self._action_service.shutdown()


class ActionServerRouteHandler:
    def __init__(self, instance_app_key: web.AppKey):
        self._instance_app_key = instance_app_key

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        return await request.app[self._instance_app_key].websocket(request)

    async def execute(self, request: web.Request) -> web.Response:
        return await request.app[self._instance_app_key].execute(request)


@web.middleware
async def _error_middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.Response]]
) -> web.Response:
    import traceback

    try:
        response = await handler(request)
        return response
    except Exception as ex:
        print(traceback.format_exc())
        print("Server Error:", str(ex))
        return web.json_response(
            {"error": "Internal Server Error", "detail": str(ex)}, status=500
        )


def make_action_server_web_app(action_server: ActionServer) -> web.Application:
    app = web.Application(middlewares=[_error_middleware])
    # Callable[["Request"], Awaitable["StreamResponse"]]
    instance_app_key = web.AppKey("instance")
    route_handler = ActionServerRouteHandler(instance_app_key)
    app.add_routes(
        [
            web.get("/websocket", route_handler.websocket),
            web.post("/execute", route_handler.execute),
        ]
    )
    # cors = aiohttp_cors.setup(app, defaults={
    #     "*": aiohttp_cors.ResourceOptions(
    #         allow_credentials=True,
    #         expose_headers="*",
    #         allow_headers="*",
    #         allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    #     )
    # })
    #
    # # Apply CORS to existing routes and handle all methods including OPTIONS
    # for route in list(app.router.routes()):
    #     cors.add(route)
    #
    # # Apply CORS to existing routes and handle all methods including OPTIONS
    # for route in list(app.router.routes()):
    #     cors.add(route)
    app[instance_app_key] = action_server
    return app
