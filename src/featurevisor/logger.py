from __future__ import annotations

from typing import Any, Callable

from .types import LogLevel

LogHandler = Callable[[LogLevel, str, dict[str, Any] | None], None]

loggerPrefix = "[Featurevisor]"


def default_log_handler(level: LogLevel, message: str, details: dict[str, Any] | None = None) -> None:
    print(loggerPrefix, message, details or {})


class Logger:
    all_levels: list[LogLevel] = ["fatal", "error", "warn", "info", "debug"]
    default_level: LogLevel = "info"

    def __init__(self, level: LogLevel | None = None, handler: LogHandler | None = None) -> None:
        self.level = level or self.default_level
        self.handle = handler or default_log_handler

    def set_level(self, level: LogLevel) -> None:
        self.level = level

    def log(self, level: LogLevel, message: str, details: dict[str, Any] | None = None) -> None:
        if self.all_levels.index(self.level) < self.all_levels.index(level):
            return
        self.handle(level, message, details)

    def debug(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.log("debug", message, details)

    def info(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.log("info", message, details)

    def warn(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.log("warn", message, details)

    def error(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.log("error", message, details)

    setLevel = set_level


def create_logger(options: dict[str, Any] | None = None) -> Logger:
    options = options or {}
    return Logger(level=options.get("level"), handler=options.get("handler"))

