import time


class Timer:
    _start: float

    def start(self):
        self._start = time.perf_counter()

    def elapsed(self):
        return time.perf_counter() - self._start
