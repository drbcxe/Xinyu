"""Compare two declared experiment results without calling an LLM."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED = (
    "run_id", "project_id", "model_name", "dataset_version", "split_id",
    "protocol_version", "metric_name", "metric_unit", "optimization",
    "metric_value", "seed",
)
COMPATIBILITY = (
    "project_id", "dataset_version", "split_id", "protocol_version",
    "metric_name", "metric_unit", "optimization", "seed",
)


class ComparisonError(ValueError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def parse_runs(raw: bytes) -> tuple[dict, str]:
    """Validate original CSV bytes for both uploads and comparisons."""
    try:
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise ComparisonError("INVALID_FILE", "CSV 不能包含 NUL 字符。")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or not set(REQUIRED).issubset(fields):
            raise ComparisonError("INVALID_SCHEMA", "缺少必需列或存在重复列名。",
                                  {"required": list(REQUIRED)})
        runs = {}
        for row in reader:
            if None in row or any(not str(row.get(key) or "").strip() for key in REQUIRED):
                raise ComparisonError("INVALID_ROW", "必需字段为空或列数不一致。",
                                      {"line": reader.line_num})
            record = {key: str(row[key]).strip() for key in REQUIRED}
            if record["run_id"] in runs:
                raise ComparisonError("DUPLICATE_RUN", "run_id 必须唯一。",
                                      {"run_id": record["run_id"], "line": reader.line_num})
            if record["optimization"] not in {"min", "max"}:
                raise ComparisonError("INVALID_DIRECTION", "optimization 必须为 min 或 max。",
                                      {"line": reader.line_num})
            try:
                value = Decimal(record["metric_value"])
                numeric = float(value)
            except (InvalidOperation, ValueError, OverflowError):
                raise ComparisonError("INVALID_METRIC", "metric_value 必须是有限数值。",
                                      {"line": reader.line_num}) from None
            if not value.is_finite() or not math.isfinite(numeric) or (value != 0 and numeric == 0):
                raise ComparisonError("INVALID_METRIC", "metric_value 超出允许数值范围。",
                                      {"line": reader.line_num})
            record["value"] = value
            record["source_line"] = reader.line_num
            runs[record["run_id"]] = record
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ComparisonError("INVALID_FILE", "无法读取有效的 UTF-8 CSV。",
                              {"reason": str(exc)}) from exc
    return runs, hashlib.sha256(raw).hexdigest()


def _load_runs(path: Path) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ComparisonError("INVALID_FILE", "无法读取有效的 UTF-8 CSV。",
                              {"reason": str(exc)}) from exc
    return parse_runs(raw)


def compare_runs(csv_path: str | Path, project_id: str,
                 baseline_id: str, candidate_id: str) -> dict:
    if baseline_id == candidate_id:
        raise ComparisonError("SAME_RUN", "请选择两个不同实验。")
    path = Path(csv_path).resolve()
    runs, content_hash = _load_runs(path)
    missing = [run_id for run_id in (baseline_id, candidate_id) if run_id not in runs]
    if missing:
        raise ComparisonError("RUN_NOT_FOUND", "找不到指定实验。", {"run_ids": missing})
    baseline, candidate = runs[baseline_id], runs[candidate_id]
    if any(run["project_id"] != project_id for run in (baseline, candidate)):
        raise ComparisonError("PROJECT_MISMATCH", "实验不属于当前项目。")
    mismatches = {
        key: {"baseline": baseline[key], "candidate": candidate[key]}
        for key in COMPATIBILITY if baseline[key] != candidate[key]
    }
    if mismatches:
        raise ComparisonError("INCOMPARABLE_RUNS", "实验评估口径不一致。", mismatches)

    delta = candidate["value"] - baseline["value"]
    improvement = -delta if baseline["optimization"] == "min" else delta
    percentage = improvement / baseline["value"] * 100 if baseline["value"] > 0 else None
    outcome = "improved" if improvement > 0 else "regressed" if improvement < 0 else "tied"

    def summarize(run: dict) -> dict:
        return {"run_id": run["run_id"], "model_name": run["model_name"],
                "value": str(run["value"]), "source_line": run["source_line"]}

    return {
        "status": "succeeded",
        "project_id": project_id,
        "metric": {"name": baseline["metric_name"], "unit": baseline["metric_unit"],
                   "optimization": baseline["optimization"]},
        "baseline": summarize(baseline),
        "candidate": summarize(candidate),
        "comparison": {
            "candidate_minus_baseline": str(delta),
            "improvement": str(improvement),
            "improvement_pct": str(percentage) if percentage is not None else None,
            "outcome": outcome,
            "better_run_id": candidate_id if improvement > 0 else baseline_id if improvement < 0 else None,
            "scope": "仅比较当前声明口径和种子，不推断统计显著性或总体最优",
        },
        "provenance": {
            "csv_path": str(path), "sha256": content_hash,
            **{key: baseline[key] for key in COMPATIBILITY if key != "project_id"},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    try:
        result = compare_runs(args.csv, args.project, args.baseline, args.candidate)
    except ComparisonError as exc:
        print(json.dumps({"status": "failed", "error": {
            "code": exc.code, "message": str(exc), "details": exc.details,
        }}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

