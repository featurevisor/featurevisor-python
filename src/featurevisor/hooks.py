from __future__ import annotations

from typing import Any

from .logger import Logger


class HooksManager:
    def __init__(self, *, hooks: list[dict[str, Any]] | None = None, logger: Logger) -> None:
        self.logger = logger
        self.hooks: list[dict[str, Any]] = []
        for hook in hooks or []:
            self.add(hook)

    def add(self, hook: dict[str, Any]):
        if any(existing_hook["name"] == hook["name"] for existing_hook in self.hooks):
            self.logger.error(f'Hook with name "{hook["name"]}" already exists.', {"name": hook["name"], "hook": hook})
            return None
        self.hooks.append(hook)

        def unsubscribe() -> None:
            self.remove(hook["name"])

        return unsubscribe

    def remove(self, name: str) -> None:
        self.hooks = [hook for hook in self.hooks if hook["name"] != name]

    def get_all(self) -> list[dict[str, Any]]:
        return list(self.hooks)

