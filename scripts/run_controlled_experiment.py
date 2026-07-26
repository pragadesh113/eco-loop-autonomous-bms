"""Run one immutable closed-loop experiment through LangGraph and FastMCP."""

from __future__ import annotations

import argparse
import sys

from bms_agent.cli import project_root
from bms_agent.integration import persist_experiment_artifacts, run_controlled_graph
from bms_agent.mcp_server.server import build_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="Safe unique immutable run ID.")
    parser.add_argument(
        "--baseline-run-id",
        default="baseline-sim002-isolated-a",
        help="Verified baseline artifact ID.",
    )
    parser.add_argument(
        "--mode",
        choices=("local-llm", "deterministic-optimizer", "deterministic-fallback"),
        required=True,
        help="Explicit advisory provider mode.",
    )
    parser.add_argument("--max-weather-timesteps", type=int, default=672)
    parser.add_argument("--max-decisions", type=int, default=168)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    if not 1 <= arguments.max_weather_timesteps <= 672:
        print("Invalid weather timestep bound.", file=sys.stderr)
        return 2
    if not 1 <= arguments.max_decisions <= 168:
        print("Invalid decision bound.", file=sys.stderr)
        return 2
    try:
        result = run_controlled_graph(
            server=build_server(),
            run_id=arguments.run_id,
            max_weather_timesteps=arguments.max_weather_timesteps,
            max_decisions=arguments.max_decisions,
            deterministic_optimization=arguments.mode == "deterministic-optimizer",
            deterministic_only=arguments.mode == "deterministic-fallback",
        )
        artifacts = persist_experiment_artifacts(
            project_root=project_root(),
            result=result,
            baseline_run_id=arguments.baseline_run_id,
        )
    except Exception:
        print(
            "Controlled experiment failed safely; inspect its immutable run artifacts.",
            file=sys.stderr,
        )
        return 2
    print(artifacts.comparison.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
