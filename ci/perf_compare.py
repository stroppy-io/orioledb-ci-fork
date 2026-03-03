#!/usr/bin/env python3
"""Compare k6/stroppy TPC-C benchmark results between base and head branches.

Parses k6 NDJSON output files (produced by stroppy-action with --out json=).
"""

import argparse
import glob
import json
import math
import os
import statistics
import sys


def percentile(sorted_data, p):
    """Compute p-th percentile from sorted data."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_data[int(k)]
    return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)


def parse_k6_json(filepath):
    """Parse k6 NDJSON results file.

    Each line is either a Metric definition or a Point data sample:
      {"type":"Point","metric":"iteration_duration","data":{"time":"...","value":12.3},"tags":{...}}
    """
    iteration_durations = []
    query_durations = []
    iteration_count = 0
    query_count = 0
    first_time = None
    last_time = None

    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "Point":
                continue

            metric = obj.get("metric", "")
            value = obj.get("data", {}).get("value")
            ts = obj.get("data", {}).get("time", "")

            if value is None:
                continue

            # Track time range for rate calculation
            if ts:
                if first_time is None or ts < first_time:
                    first_time = ts
                if last_time is None or ts > last_time:
                    last_time = ts

            if metric == "iteration_duration":
                iteration_durations.append(float(value))
                iteration_count += 1
            elif metric == "run_query_duration":
                query_durations.append(float(value))
                query_count += 1

    # Estimate duration in seconds from time range
    duration_s = estimate_duration_s(first_time, last_time)

    metrics = {}

    if iteration_count > 0:
        iteration_durations.sort()
        metrics["total_iterations"] = iteration_count
        metrics["total_iterations_rate"] = iteration_count / duration_s if duration_s > 0 else 0
        metrics["avg_duration_ms"] = statistics.mean(iteration_durations)
        metrics["med_duration_ms"] = statistics.median(iteration_durations)
        metrics["p90_duration_ms"] = percentile(iteration_durations, 90)
        metrics["p95_duration_ms"] = percentile(iteration_durations, 95)

    if query_count > 0:
        query_durations.sort()
        metrics["query_rate"] = query_count / duration_s if duration_s > 0 else 0
        metrics["query_avg_ms"] = statistics.mean(query_durations)
        metrics["query_p90_ms"] = percentile(query_durations, 90)
        metrics["query_p95_ms"] = percentile(query_durations, 95)

    return metrics


def estimate_duration_s(first_time, last_time):
    """Estimate test duration from ISO timestamps."""
    if not first_time or not last_time:
        return 1.0
    from datetime import datetime, timezone

    def parse_ts(s):
        # Handle various k6 timestamp formats
        s = s.replace("Z", "+00:00")
        # Truncate nanosecond precision to microsecond
        if "." in s:
            base, frac_and_tz = s.split(".", 1)
            # Separate fractional seconds from timezone
            for i, c in enumerate(frac_and_tz):
                if c in ("+", "-") and i > 0:
                    frac = frac_and_tz[:i][:6]  # max 6 digits
                    tz = frac_and_tz[i:]
                    s = f"{base}.{frac}{tz}"
                    break
            else:
                frac = frac_and_tz[:6]
                s = f"{base}.{frac}"
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    t1 = parse_ts(first_time)
    t2 = parse_ts(last_time)
    if t1 and t2:
        return max((t2 - t1).total_seconds(), 1.0)
    return 1.0


def find_result_files(results_dir, num_runs):
    """Find stroppy JSON result files in the download directory.

    stroppy-action artifacts are downloaded as:
      results-dir/perf-results-{branch}-{N}/stroppy-results.json
    """
    files = sorted(glob.glob(os.path.join(results_dir, "**", "stroppy-results.json"), recursive=True))
    if not files:
        # Fallback: try flat layout
        files = sorted(glob.glob(os.path.join(results_dir, "*.json")))
    return files[:num_runs]


def load_run_results(results_dir, num_runs):
    """Load and parse all result files from a results directory."""
    files = find_result_files(results_dir, num_runs)
    all_metrics = []
    for filepath in files:
        print(f"Parsing: {filepath}", file=sys.stderr)
        metrics = parse_k6_json(filepath)
        if metrics:
            all_metrics.append(metrics)
        else:
            print(f"Warning: no metrics found in {filepath}", file=sys.stderr)
    return all_metrics


def compute_medians(all_metrics):
    """Compute median values across all runs for each metric."""
    if not all_metrics:
        return {}
    keys = set()
    for m in all_metrics:
        keys.update(m.keys())
    medians = {}
    for key in sorted(keys):
        values = [m[key] for m in all_metrics if key in m]
        if values:
            medians[key] = statistics.median(values)
    return medians


def format_change(base_val, head_val, lower_is_better=False):
    """Format percentage change with direction indicator."""
    if base_val == 0:
        return "N/A"
    change = (head_val - base_val) / base_val * 100
    sign = "+" if change > 0 else ""
    if lower_is_better:
        indicator = " :white_check_mark:" if change < -2 else (" :warning:" if change > 2 else "")
    else:
        indicator = " :white_check_mark:" if change > 2 else (" :warning:" if change < -2 else "")
    return f"{sign}{change:.1f}%{indicator}"


def format_value(value, is_rate=False):
    """Format a metric value for display."""
    if is_rate:
        return f"{value:.1f}/s"
    return f"{value:.1f}ms"


def generate_markdown(base_medians, head_medians, config):
    """Generate markdown comparison table."""
    lines = []
    lines.append("## Performance Test Results (TPC-C)")
    lines.append("")
    lines.append("| Metric | Base | Head | Change |")
    lines.append("|--------|------|------|--------|")

    # Throughput metrics (higher is better)
    rate_metrics = [
        ("total_iterations_rate", "Iterations/s"),
        ("query_rate", "Queries/s"),
    ]

    for key, label in rate_metrics:
        base_val = base_medians.get(key)
        head_val = head_medians.get(key)
        if base_val is not None and head_val is not None:
            lines.append(
                f"| {label} | {format_value(base_val, is_rate=True)} "
                f"| {format_value(head_val, is_rate=True)} "
                f"| {format_change(base_val, head_val)} |"
            )

    # Count metric
    base_iters = base_medians.get("total_iterations")
    head_iters = head_medians.get("total_iterations")
    if base_iters is not None and head_iters is not None:
        lines.append(
            f"| Total iterations | {int(base_iters)} "
            f"| {int(head_iters)} "
            f"| {format_change(base_iters, head_iters)} |"
        )

    # Duration metrics (lower is better)
    duration_metrics = [
        ("avg_duration_ms", "Avg iteration duration"),
        ("med_duration_ms", "Median iteration duration"),
        ("p90_duration_ms", "P90 iteration duration"),
        ("p95_duration_ms", "P95 iteration duration"),
        ("query_avg_ms", "Avg query duration"),
        ("query_p90_ms", "P90 query duration"),
        ("query_p95_ms", "P95 query duration"),
    ]

    for key, label in duration_metrics:
        base_val = base_medians.get(key)
        head_val = head_medians.get(key)
        if base_val is not None and head_val is not None and (base_val > 0 or head_val > 0):
            lines.append(
                f"| {label} | {format_value(base_val)} "
                f"| {format_value(head_val)} "
                f"| {format_change(base_val, head_val, lower_is_better=True)} |"
            )

    lines.append("")
    lines.append(
        f"**Config**: {config['runs']} runs, {config['duration']} each, "
        f"scale_factor={config['scale_factor']}"
    )
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Compare TPC-C benchmark results")
    parser.add_argument("--base-dir", required=True, help="Directory with base branch results")
    parser.add_argument("--head-dir", required=True, help="Directory with head branch results")
    parser.add_argument("--runs", type=int, default=5, help="Number of benchmark runs")
    parser.add_argument("--duration", default="10m", help="Duration per run")
    parser.add_argument("--scale-factor", default="1", help="TPC-C scale factor")
    parser.add_argument("--output", default="comment.md", help="Output markdown file")
    args = parser.parse_args()

    base_metrics = load_run_results(args.base_dir, args.runs)
    head_metrics = load_run_results(args.head_dir, args.runs)

    if not base_metrics:
        print("Error: no base branch results found", file=sys.stderr)
        sys.exit(1)
    if not head_metrics:
        print("Error: no head branch results found", file=sys.stderr)
        sys.exit(1)

    base_medians = compute_medians(base_metrics)
    head_medians = compute_medians(head_metrics)

    config = {
        "runs": args.runs,
        "duration": args.duration,
        "scale_factor": args.scale_factor,
    }

    markdown = generate_markdown(base_medians, head_medians, config)
    with open(args.output, "w") as f:
        f.write(markdown)

    print(markdown)


if __name__ == "__main__":
    main()
