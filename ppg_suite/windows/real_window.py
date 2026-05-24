from __future__ import annotations

from .measurement_window import PPGSuite


class RealWindow(PPGSuite):
    def __init__(self):
        super().__init__("real")
