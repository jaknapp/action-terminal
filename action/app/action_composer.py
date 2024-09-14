from typing import Tuple

from aiohttp import web

from action.app.action_server import ActionServer, make_action_server_web_app
from action.app.action_server_runner import ActionServerRunner
from action.app.action_service import ActionService


class ActionComposer:
    def compose_action_server_web_app(self) -> Tuple[ActionServer, web.Application]:
        action_service = ActionService()
        action_server = ActionServer(action_service)
        app = make_action_server_web_app(action_server)
        return action_server, app

    def compose_server_runner(self) -> ActionServerRunner:
        action_service = ActionService()
        action_server = ActionServer(action_service)
        app = make_action_server_web_app(action_server)
        return ActionServerRunner(app=app, port=5001)


def run_action_server(extra_argv=None):
    action_composer = ActionComposer()
    action_server_runner = action_composer.compose_server_runner()
    action_server_runner.run()


if __name__ == "__main__":
    run_action_server()
