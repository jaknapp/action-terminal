from aiohttp import web


class ActionServerRunner:
    def __init__(self, app: web.Application, port: int):
        self._app = app
        self._port = port

    def run(self) -> None:
        web.run_app(self._app, host="0.0.0.0", port=self._port)
