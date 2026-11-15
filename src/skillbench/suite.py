"""Turning tests into the runs an invocation makes (one per trigger prompt, backend and repeat),
and judging each test from its runs."""

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from skillbench.config import TestSpec
from skillbench.runner import RunOptions

_TRIGGER_PROMPT_SHOWN_CHARS = 40


@dataclass(frozen=True)
class PlannedRun:
    spec: TestSpec
    options: RunOptions
    repeat_index: int

    @property
    def key(self) -> tuple[str, str]:
        """Runs with the same key are repeats of one test on one backend."""
        return self.spec.name, self.options.installed.backend.name


@dataclass(frozen=True)
class Plan:
    runs: list[PlannedRun]
    skipped: list[tuple[TestSpec, str]]  # (test, backend) pairs the test's `backends:` excludes
    test_count: int  # tests after trigger prompts are split out, per backend and repeat
    backend_count: int
    repeat_counts: list[int]  # each test's repeats

    def describe(self) -> str:
        """e.g. `6 tests × 5 repeats × 2 backends = 60 runs`."""
        parts = [_plural(self.test_count, "test")]
        uniform_repeat = len(set(self.repeat_counts)) == 1 and self.repeat_counts[0] > 1
        if uniform_repeat:
            parts.append(_plural(self.repeat_counts[0], "repeat"))
        elif any(count > 1 for count in self.repeat_counts):
            parts[0] += " (some repeated)"
        if self.backend_count > 1:
            parts.append(_plural(self.backend_count, "backend"))
        return f"{' × '.join(parts)} = {_plural(len(self.runs), 'run')}"


def plan_runs(
    specs: list[TestSpec], all_options: list[RunOptions], repeat_override: int | None
) -> Plan:
    runs: list[PlannedRun] = []
    skipped: list[tuple[TestSpec, str]] = []
    variants = [variant for spec in specs for variant in _variants(spec, repeat_override)]
    for variant in variants:
        for options in all_options:
            backend_name = options.installed.backend.name
            if not variant.runs_on(backend_name):
                skipped.append((variant, backend_name))
                continue
            runs += [PlannedRun(variant, options, index) for index in range(1, variant.repeat + 1)]
    return Plan(
        runs=runs,
        skipped=skipped,
        test_count=len(variants),
        backend_count=len(all_options),
        repeat_counts=[variant.repeat for variant in variants] or [1],
    )


def _variants(spec: TestSpec, repeat_override: int | None) -> list[TestSpec]:
    """The test itself (unless it only has trigger prompts), then one test per trigger prompt."""
    if repeat_override is not None:
        spec = replace(spec, repeat=repeat_override)
    variants = [spec] if spec.has_forced_run else []
    for case in spec.triggers:
        label = "should" if case.should_invoke else "should not"
        prompt = case.prompt
        if len(prompt) > _TRIGGER_PROMPT_SHOWN_CHARS:
            prompt = prompt[: _TRIGGER_PROMPT_SHOWN_CHARS - 3] + "..."
        variants.append(
            replace(spec, name=f"{spec.name} [{label}] {prompt}", trigger=case, triggers=[])
        )
    return variants


@dataclass
class GroupResult:
    """One test on one backend, judged from all its repeats."""

    test_name: str
    backend: str
    min_pass_rate: float
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(1 for record in self.records if record["status"] == "pass")

    @property
    def is_repeated(self) -> bool:
        return len(self.records) > 1

    @property
    def status(self) -> str:
        if not self.is_repeated:
            return self.records[0]["status"]
        if self.passes / len(self.records) >= self.min_pass_rate:
            return "pass"
        if all(record["status"] == "error" for record in self.records):
            return "error"
        return "fail"

    def assertion_hits(self) -> list[tuple[str, int]]:
        """Each assertion, labelled `type: expected`, and in how many runs it passed."""
        hits: Counter[str] = Counter()
        labels: list[str] = []
        for record in self.records:
            for result in record["assertions"]:
                label = f"{result['type']}: {_format_expected(result['expected'])}"
                if label not in labels:
                    labels.append(label)
                hits[label] += 1 if result["passed"] else 0
        return [(label, hits[label]) for label in labels]


def collect_results(runs: list[PlannedRun], records: list[dict[str, Any]]) -> list[GroupResult]:
    results: dict[tuple[str, str], GroupResult] = {}
    for run, record in zip(runs, records, strict=True):
        name, backend = run.key
        result = results.setdefault(
            run.key, GroupResult(name, backend, min_pass_rate=run.spec.min_pass_rate)
        )
        result.records.append(record)
    return list(results.values())


def _format_expected(expected: Any) -> str:
    if isinstance(expected, list):
        return " -> ".join(map(str, expected))
    if isinstance(expected, dict):
        return ", ".join(f"{key}={value}" for key, value in expected.items())
    return str(expected)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
