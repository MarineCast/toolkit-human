from __future__ import annotations

from dataclasses import dataclass
from graphlib import TopologicalSorter
from typing import Callable

from .contracts import StageRequest, StageResult


@dataclass(frozen=True)
class DataStage:
    name: str
    runner: Callable[[StageRequest], StageResult]
    dependencies: tuple[str, ...] = ()


class DataDAG:
    def __init__(self, stages: tuple[DataStage, ...]):
        self.stages = {stage.name: stage for stage in stages}
        TopologicalSorter(
            {name: set(stage.dependencies) for name, stage in self.stages.items()}
        ).prepare()

    def order(self, selected: tuple[str, ...] = ()) -> tuple[str, ...]:
        requested = set(selected or self.stages)
        closure = set(requested)
        pending = list(requested)
        while pending:
            name = pending.pop()
            if name not in self.stages:
                raise KeyError(f"Unknown data stage: {name}")
            for dependency in self.stages[name].dependencies:
                if dependency not in closure:
                    closure.add(dependency)
                    pending.append(dependency)
        ordered = TopologicalSorter(
            {name: set(self.stages[name].dependencies) & closure for name in closure}
        ).static_order()
        return tuple(ordered)

    def run(self, request: StageRequest, selected: tuple[str, ...] = ()) -> dict[str, StageResult]:
        results: dict[str, StageResult] = {}
        for name in self.order(selected):
            dependency_outputs = tuple(
                output
                for dependency in self.stages[name].dependencies
                for output in results[dependency].outputs
            )
            stage_request = StageRequest(**{**request.__dict__, "inputs": dependency_outputs})
            results[name] = self.stages[name].runner(stage_request)
        return results
