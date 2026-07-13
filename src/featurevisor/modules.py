from __future__ import annotations

import uuid
from typing import Any, Callable

DiagnosticHandler = Callable[[dict[str, Any]], None]


class FeaturevisorModule:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        options = options or {}
        self.id = str(uuid.uuid4())
        self.name = options.get("name")
        self.setup = options.get("setup")
        self.before = options.get("before")
        self.bucket_key = options.get("bucketKey") or options.get("bucket_key")
        self.bucket_value = options.get("bucketValue") or options.get("bucket_value")
        self.after = options.get("after")
        self.close = options.get("close")

    def call_setup(self, api: dict[str, Any]) -> None:
        if self.setup:
            self.setup(api)

    def call_before(self, options: dict[str, Any]) -> dict[str, Any]:
        if not self.before:
            return options
        return self.before(options)

    def call_bucket_key(self, options: dict[str, Any]) -> str:
        if not self.bucket_key:
            return options["bucketKey"]
        return self.bucket_key(options)

    def call_bucket_value(self, options: dict[str, Any]) -> int:
        if not self.bucket_value:
            return options["bucketValue"]
        return self.bucket_value(options)

    def call_after(self, evaluation: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        if not self.after:
            return evaluation
        return self.after(evaluation, options)

    def call_close(self) -> None:
        if self.close:
            self.close()


class ModulesManager:
    def __init__(
        self,
        *,
        modules: list[dict[str, Any] | FeaturevisorModule] | None = None,
        report_diagnostic: Callable[[dict[str, Any], FeaturevisorModule | None], None] | None = None,
        module_api_factory: Callable[[FeaturevisorModule], dict[str, Any]] | None = None,
        clear_module_diagnostic_subscriptions: Callable[[FeaturevisorModule], None] | None = None,
    ) -> None:
        self.modules: list[FeaturevisorModule] = []
        self.report_diagnostic = report_diagnostic
        self.module_api_factory = module_api_factory
        self.clear_module_diagnostic_subscriptions = clear_module_diagnostic_subscriptions

        for module in modules or []:
            self.add(module)

    def add(self, module: dict[str, Any] | FeaturevisorModule):
        module = FeaturevisorModule(module) if isinstance(module, dict) else module
        if module is None:
            return None

        if module.name and any(existing.name == module.name for existing in self.modules):
            self._report(
                {
                    "level": "error",
                    "code": "duplicate_module",
                    "message": "Duplicate module name",
                    "moduleName": module.name,
                },
                None,
            )
            return None

        if self.module_api_factory:
            try:
                module.call_setup(self.module_api_factory(module))
            except Exception as exc:
                if self.clear_module_diagnostic_subscriptions:
                    self.clear_module_diagnostic_subscriptions(module)
                self._report(
                    {
                        "level": "error",
                        "code": "module_setup_error",
                        "message": "Module setup failed",
                        "moduleName": module.name,
                        "originalError": exc,
                    },
                    None,
                )
                self._close_module(module)
                return None

        self.modules.append(module)

        def remove() -> None:
            self.remove(module)

        return remove

    def remove(self, name_or_module: str | FeaturevisorModule) -> None:
        removed: list[FeaturevisorModule] = []
        kept: list[FeaturevisorModule] = []
        for module in self.modules:
            matches = module is name_or_module or module.name == name_or_module
            if matches:
                removed.append(module)
            else:
                kept.append(module)
        self.modules = kept

        for module in removed:
            if self.clear_module_diagnostic_subscriptions:
                self.clear_module_diagnostic_subscriptions(module)
            self._close_module(module)

    def get_all(self) -> list[FeaturevisorModule]:
        return list(self.modules)

    def run_before_modules(self, options: dict[str, Any]) -> dict[str, Any]:
        current = options
        for module in self.modules:
            current = module.call_before(current)
        return current

    def run_bucket_key_modules(self, options: dict[str, Any]) -> str:
        bucket_key = options["bucketKey"]
        for module in self.modules:
            bucket_key = module.call_bucket_key({**options, "bucketKey": bucket_key})
        return bucket_key

    def run_bucket_value_modules(self, options: dict[str, Any]) -> int:
        bucket_value = options["bucketValue"]
        for module in self.modules:
            bucket_value = module.call_bucket_value({**options, "bucketValue": bucket_value})
        return bucket_value

    def run_after_modules(self, evaluation: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        current = evaluation
        for module in self.modules:
            current = module.call_after(current, options)
        return current

    def close_all(self) -> None:
        for module in self.modules:
            if self.clear_module_diagnostic_subscriptions:
                self.clear_module_diagnostic_subscriptions(module)
            self._close_module(module)
        self.modules = []

    def _close_module(self, module: FeaturevisorModule) -> None:
        try:
            module.call_close()
        except Exception as exc:
            self._report(
                {
                    "level": "error",
                    "code": "module_close_error",
                    "message": "Module close failed",
                    "moduleName": module.name,
                    "originalError": exc,
                },
                None,
            )

    def _report(self, diagnostic: dict[str, Any], module: FeaturevisorModule | None = None) -> None:
        if self.report_diagnostic:
            self.report_diagnostic(diagnostic, module)

    getAll = get_all
    runBeforeModules = run_before_modules
    runBucketKeyModules = run_bucket_key_modules
    runBucketValueModules = run_bucket_value_modules
    runAfterModules = run_after_modules
    closeAll = close_all
