from __future__ import annotations

from .measurement_window import PPGSuite


class TestWindow(PPGSuite):
    def __init__(self):
        super().__init__("test")
