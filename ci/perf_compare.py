#!/usr/bin/env python3
"""Compare k6/stroppy TPC-C benchmark results between base and head branches."""

import argparse
import os
import re
import statistics
import sys


def parse_k6_metrics(log_text):
    """Parse k6 end-of-test summary from a stroppy log file.

    Extracts metrics from k6 output format. Duration values may use
    unicode micro sign (µs) so we use a non-greedy pattern for units.
    """
    metrics = {}

    # Duration value pattern: number followed by unit (handles µs, ms, s, m)
    DUR = r"([\d.]+[a-zµ\u00b5]+)"

    # Parse aggregate iterations rate
    m = re.search(
        r"iterations\.+:\s+(\d+)\s+([\d.]+)/s",
        log_text,
    )
    if m:
        metrics["total_iterations"] = int(m.group(1))
        metrics["total_iterations_rate"] = float(m.group(2))

    # Parse aggregate iteration_duration
    pat = (
        r"iteration_duration\.+:\s+"
        rf"avg={DUR}\s+min={DUR}\s+med={DUR}\s+max={DUR}\s+"
        rf"p\(90\)={DUR}\s+p\(95\)={DUR}"
    )
    m = re.search(pat, log_text)
    if m:
        metrics["avg_duration_ms"] = parse_duration_ms(m.group(1))
        metrics["med_duration_ms"] = parse_duration_ms(m.group(3))
        metrics["p90_duration_ms"] = parse_duration_ms(m.group(5))
        metrics["p95_duration_ms"] = parse_duration_ms(m.group(6))

    # Parse run_query_duration (stroppy custom metric)
    pat_rq = (
        r"run_query_duration\.+:\s+"
        rf"avg={DUR}\s+min={DUR}\s+med={DUR}\s+max={DUR}\s+"
        rf"p\(90\)={DUR}\s+p\(95\)={DUR}"
    )
    m = re.search(pat_rq, log_text)
    if m:
        metrics["query_avg_ms"] = parse_duration_ms(m.group(1))
        metrics["query_p90_ms"] = parse_duration_ms(m.group(5))
        metrics["query_p95_ms"] = parse_duration_ms(m.group(6))

    # Parse run_query_count (queries/s)
    m = re.search(r"run_query_count\.+:\s+(\d+)\s+([\d.]+)/s", log_text)
    if m:
        metrics["query_rate"] = float(m.group(2))

    return metrics


def parse_duration_ms(s):
    """Convert a k6 duration string (e.g., '12.3ms', '1.5s', '77.41µs') to milliseconds."""
    m = re.match(r"([\d.]+)(.*)", s)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2).strip()
    if unit == "ms":
        return value
    elif unit == "s":
        return value * 1000
    elif unit in ("us", "\u00b5s", "\xb5s", "µs"):
        return value / 1000
    elif unit == "m":
        return value * 60000
    return value


def load_run_results(results_dir, num_runs):
    """Load and parse all run log files from a results directory."""
    all_metrics = []
    for i in range(1, num_runs + 1):
        log_path = os.path.join(results_dir, f"run_{i}.log")
        if not os.path.exists(log_path):
            print(f"Warning: {log_path} not found, skipping", file=sys.stderr)
            continue
        with open(log_path) as f:
            text = f.read()
        metrics = parse_k6_metrics(text)
        if metrics:
            all_metrics.append(metrics)
        else:
            print(f"Warning: no metrics found in {log_path}", file=sys.stderr)
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
    # For durations, lower is better; for throughput, higher is better
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
