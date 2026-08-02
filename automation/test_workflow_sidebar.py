"""Testes sem navegador para o comportamento da coluna lateral do Vibes."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from automation.workflow import AnimationWorkflow


class _StaticSidebarButton:
    async def evaluate(self, _script: str) -> dict[str, object]:
        return {
            "found": True,
            "scrollable": False,
            "top": 0,
            "height": 540,
            "client": 540,
            "atBottom": True,
        }


class _SidebarButtons:
    async def evaluate_all(self, _script: str) -> list[str]:
        return ["first.png", "second.png"]


class _Page:
    async def wait_for_timeout(self, _milliseconds: int) -> None:
        return None


class _Recorder:
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class SidebarTraversalTest(unittest.TestCase):
    def test_static_sidebar_is_already_at_its_bottom(self) -> None:
        workflow = object.__new__(AnimationWorkflow)
        workflow.page = _Page()
        workflow.settings = SimpleNamespace(timeout_ms=1_000)
        workflow.logger = _Recorder()
        workflow.audit = _Recorder()

        async def visible(*_args, **_kwargs):
            return _StaticSidebarButton()

        with (
            patch("automation.workflow.first_visible", visible),
            patch("automation.workflow.EDITOR_SIDEBAR_THUMBNAILS", (lambda _page: _SidebarButtons(),)),
        ):
            names = asyncio.run(workflow._scroll_editor_sidebar_to_bottom())

        self.assertEqual(names, ["first.png", "second.png"])


if __name__ == "__main__":
    unittest.main()
