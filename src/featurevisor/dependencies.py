from __future__ import annotations

from typing import Any

from ._json import strict_json_loads


def _condition_dependencies(expression: Any, sources: dict) -> None:
    if isinstance(expression, str):
        if expression.lstrip()[:1] in ('{', '[', '"'):
            try:
                _condition_dependencies(strict_json_loads(expression), sources)
            except (TypeError, ValueError):
                pass
        elif expression != "*":
            sources[("segment", expression)] = None
    elif isinstance(expression, list):
        for item in expression:
            _condition_dependencies(item, sources)
    elif isinstance(expression, dict):
        if "variable" in expression:
            if expression.get("feature") is not None:
                sources[("feature", expression["feature"])] = None
            else:
                sources[("variable", expression["variable"])] = None
        elif "feature" in expression:
            sources[("feature", expression["feature"])] = None
        else:
            for operator in ("and", "or", "not"):
                if operator in expression:
                    _condition_dependencies(expression[operator], sources)


def _entity_dependencies(entry: Any, sources: dict) -> None:
    if not isinstance(entry, dict):
        return
    if "conditions" in entry:
        _condition_dependencies(entry["conditions"], sources)
    # Most entries have no nested selectors. Avoid allocating empty containers
    # for absent fields while preserving the authored traversal order.
    for name in ("force", "traffic", "variations", "overrides", "overrideProgram"):
        if name in entry:
            for child in entry[name] or ():
                _entity_dependencies(child, sources)
    if "variableOverrides" in entry:
        for overrides in (entry["variableOverrides"] or {}).values():
            for override in overrides:
                _entity_dependencies(override, sources)


def extract_entity_dependencies(definition: dict[str, Any]) -> tuple:
    sources: dict[tuple[str, str], None] = {}
    _entity_dependencies(definition, sources)
    compose = definition.get("compose")
    if isinstance(compose, str):
        sources[("variable", compose)] = None
    elif isinstance(compose, list):
        for reference in compose:
            if reference.get("feature") is not None:
                sources[("feature", reference["feature"])] = None
            elif reference.get("variable") is not None:
                sources[("variable", reference["variable"])] = None
    return tuple(sources)


COLLECTIONS = (("segments", "segment"), ("features", "feature"), ("variables", "variable"))


class DatafileDependencyIndex:
    """Retain edge identities, never historical providers or definitions."""

    def __init__(self) -> None:
        self.reverse: dict[tuple, dict[tuple, None]] = {}
        self.owners: dict[tuple, tuple] = {}
        self.order: dict[tuple, int] = {}
        self.next_order = 0

    def update(self, datafile: dict, incoming: dict, previous: dict | None = None) -> None:
        # Extract first so failures cannot partially modify the graph.
        updates = []
        for name, kind in COLLECTIONS:
            definitions = datafile.get(name) or {}
            previous_definitions = (previous or {}).get(name) or {}
            for key in incoming.get(name, {}):
                definition = definitions.get(key)
                owner = (kind, key)
                if definition is not None and owner in self.owners:
                    old = previous_definitions.get(key)
                    try:
                        if old is definition or old == definition:
                            continue
                    except RecursionError:
                        # Recursive native metadata need not be comparable.
                        pass
                updates.append((owner, None if definition is None else extract_entity_dependencies(definition)))
        for owner, sources in updates:
            for source in self.owners.pop(owner, ()):
                dependents = self.reverse[source]
                del dependents[owner]
                if not dependents:
                    del self.reverse[source]
            if sources is None:
                self.order.pop(owner, None)
                continue
            if owner not in self.order:
                self.order[owner] = self.next_order
                self.next_order += 1
            self.owners[owner] = sources
            for source in sources:
                self.reverse.setdefault(source, {})[owner] = None

    def get(self, identity, default=()):
        dependents = self.reverse.get(identity)
        return sorted(dependents, key=self.order.__getitem__) if dependents else default

    def reorder(self, datafile: dict) -> None:
        self.order = {(kind, key): rank for name, kind in COLLECTIONS
                      for rank, key in enumerate(datafile.get(name) or {})}
        self.next_order = max(self.order.values(), default=-1) + 1


def create_datafile_dependency_index(datafile: dict[str, Any]) -> DatafileDependencyIndex:
    index = DatafileDependencyIndex()
    index.update(datafile, datafile)
    return index


def add_datafile_dependency_changes(features, variables, changed_segments, index) -> None:
    kinds = ("feature", "variable", "segment")
    affected = [dict.fromkeys(features), dict.fromkeys(variables), dict.fromkeys(changed_segments)]
    queues = [list(values) for values in affected]
    cursors = [0, 0, 0]
    while True:
        advanced = False
        for source, kind in enumerate(kinds):
            if cursors[source] >= len(queues[source]):
                continue
            advanced = True
            key = queues[source][cursors[source]]
            cursors[source] += 1
            for target_kind, target_key in index.get((kind, key), ()):
                target = kinds.index(target_kind)
                if target_key not in affected[target]:
                    affected[target][target_key] = None
                    queues[target].append(target_key)
        if not advanced:
            break
    features[:] = affected[0]
    variables[:] = affected[1]
