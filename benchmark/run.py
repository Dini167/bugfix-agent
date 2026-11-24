"""Run both agents over the benchmark and write the results.

    bugfix benchmark                      # both agents, all cases, 3 repeats
    bugfix benchmark --engine raw --cases 01 11 --repeats 1

Writes `benchmark/results/results.json` (every run) and `results.md`
(summary tables), plus one JSONL trace per run under `results/runs/`.
LLM runs are not deterministic, so each case runs several times.
"""

# 📘 הסקריפט שמריץ את שני האיגנטים על כל הבאגים, כמה פעמים כל אחד, ומפיק טבלת השוואה. כל ריצה
# 📘 מריצה את אותו באג מאפס, ולכן התוצאות בלתי תלויות זו בזו.

from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from benchmark.catalog import Case
from benchmark.loc import layer_counts
from cli.engines import open_engine
from core.config import Settings
from core.harness import run_case
from core.types import RunResult, RunStatus

RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass(frozen=True, slots=True)
class Summary:
    agent: str
    runs: int
    resolved: int
    mean_iterations: float
    mean_input_tokens: float
    mean_output_tokens: float
    mean_cost_usd: float
    mean_wall_time_s: float
    tampered: int
    budget_exceeded: int
    errors: int

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.runs if self.runs else 0.0


def summarize(agent: str, results: list[RunResult]) -> Summary:
    def mean(values: list[float]) -> float:
        return round(statistics.fmean(values), 4) if values else 0.0

    return Summary(
        agent=agent,
        runs=len(results),
        resolved=sum(r.resolved for r in results),
        mean_iterations=mean([r.iterations for r in results]),
        mean_input_tokens=mean(
            [r.usage.input_tokens + r.usage.cache_read_input_tokens for r in results]
        ),
        mean_output_tokens=mean([r.usage.output_tokens for r in results]),
        mean_cost_usd=mean([r.cost_usd for r in results]),
        mean_wall_time_s=mean([r.wall_time_s for r in results]),
        tampered=sum(r.status is RunStatus.TESTS_TAMPERED for r in results),
        budget_exceeded=sum(r.status is RunStatus.BUDGET_EXCEEDED for r in results),
        errors=sum(r.status is RunStatus.ERROR for r in results),
    )


def run_benchmark(
    engines: list[str],
    cases: list[Case],
    repeats: int,
    settings: Settings,
    on_result: Callable[[RunResult], None] | None = None,
    out_dir: Path = RESULTS_DIR,
) -> dict[str, list[RunResult]]:
    results: dict[str, list[RunResult]] = {name: [] for name in engines}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for name in engines:
        with open_engine(name, settings) as engine:
            for case in cases:
                for attempt in range(1, repeats + 1):
                    trace = out_dir / "runs" / stamp / f"{name}-{case.id}-{attempt}.jsonl"
                    result = run_case(case.id, case.repo, engine, settings, trace_path=trace)
                    results[name].append(result)
                    if on_result:
                        on_result(result)
    write_reports(results, cases, repeats, settings, out_dir)
    return results


def write_reports(
    results: dict[str, list[RunResult]],
    cases: list[Case],
    repeats: int,
    settings: Settings,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": settings.model,
        "effort": settings.effort,
        "repeats": repeats,
        "summaries": {name: asdict(summarize(name, runs)) for name, runs in results.items()},
        "runs": {name: [r.to_dict() for r in runs] for name, runs in results.items()},
    }
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "results.md").write_text(render_markdown(results, cases, settings), "utf-8")


def render_markdown(
    results: dict[str, list[RunResult]], cases: list[Case], settings: Settings
) -> str:
    lines = [
        "# Benchmark results",
        "",
        f"Model `{settings.model}`, effort `{settings.effort}`. "
        "A run counts as resolved only if the grader's own test run passes and no test "
        "or test-configuration file changed.",
        "",
        "## Summary",
        "",
        "| Agent | Resolved | Mean iterations | Mean input tokens | Mean output tokens "
        "| Mean cost (USD) | Mean time (s) | Tampered | Budget hit | Errors |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, runs in results.items():
        s = summarize(name, runs)
        lines.append(
            f"| {name} | {s.resolved}/{s.runs} ({s.resolve_rate:.0%}) | {s.mean_iterations:.1f} "
            f"| {s.mean_input_tokens:,.0f} | {s.mean_output_tokens:,.0f} "
            f"| {s.mean_cost_usd:.4f} | {s.mean_wall_time_s:.1f} | {s.tampered} "
            f"| {s.budget_exceeded} | {s.errors} |"
        )
    names = list(results)
    lines += [
        "",
        "## Per case",
        "",
        "| Case | Difficulty | " + " | ".join(names) + " |",
        "|---|---|" + "---|" * len(names),
    ]
    for case in cases:
        cells = []
        for name in names:
            runs = [r for r in results[name] if r.case_id == case.id]
            cells.append(f"{sum(r.resolved for r in runs)}/{len(runs)}")
        lines.append(f"| {case.id} | {case.difficulty} | " + " | ".join(cells) + " |")
    lines += ["", "## Lines of code (excluding comments and docstrings)", ""]
    lines += ["| Implementation | Layer | Lines |", "|---|---|---|"]
    for impl, layers in layer_counts().items():
        for layer, count in layers.items():
            lines.append(f"| {impl} | {layer} | {count} |")
    return "\n".join(lines) + "\n"
