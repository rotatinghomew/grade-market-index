"""稳定的函数式入口，供 notebook 或其他脚本调用。

示例：
    from analysis import analyze_directory
    report = analyze_directory("data", quarter="2026-Q2")
"""

from __future__ import annotations

from pathlib import Path

from quality_index.core import Project, clean_for_json

__all__ = ["Project", "analyze_directory", "trace_target"]


def analyze_directory(directory: str | Path = "data", quarter: str | None = "2026-Q2"):
    project = Project.load(directory)
    return project.full_report(quarter)


def trace_target(directory: str | Path, target_id: str, quarter: str = "2026-Q2"):
    project = Project.load(directory)
    return clean_for_json(project.evaluate_target(target_id, quarter=quarter))
