from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from .types import EventName

EventCallback = Callable[[dict[str, Any]], None]


class Emitter:
    def __init__(self) -> None:
        self.listeners: dict[str, list[EventCallback]] = defaultdict(list)

    def on(self, event_name: EventName, callback: EventCallback) -> Callable[[], None]:
        listeners = self.listeners[event_name]
        listeners.append(callback)
        active = True

        def unsubscribe() -> None:
            nonlocal active
            if not active:
                return
            active = False
            try:
                listeners.remove(callback)
            except ValueError:
                return

        return unsubscribe

    def trigger(self, event_name: EventName, details: dict[str, Any] | None = None) -> None:
        for listener in list(self.listeners.get(event_name, [])):
            try:
                listener(details or {})
            except Exception as exc:
                print(exc)

    def clear_all(self) -> None:
        self.listeners = defaultdict(list)

    clearAll = clear_all

