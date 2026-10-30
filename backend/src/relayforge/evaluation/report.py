"""Portable report regeneration from actual raw JSONL observations."""

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from relayforge.evaluation.metrics import summarize
from relayforge.workspaces.manager import reject_links


def atomic_text(path: Path, content: str):
    reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    reject_links(temporary)
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_reports(destination: Path, records: list[dict], metadata: dict):
    reject_links(destination)
    destination.mkdir(parents=True, exist_ok=True)
    summary = summarize(records)
    metadata = {
        **metadata,
        "report_generator": {
            "schema_version": 1,
            "metrics_sha256": hashlib.sha256(
                Path(__file__).with_name("metrics.py").read_bytes()
            ).hexdigest(),
            "report_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    }
    atomic_text(destination / "manifest.json", json.dumps(metadata, indent=2, ensure_ascii=False))
    atomic_text(
        destination / "results.jsonl",
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
    )
    columns = [
        "case_id",
        "mode",
        "template",
        "fault",
        "seed",
        "run_id",
        "run_status",
        "actual_external_effects",
        "external_requests",
        "unsafe_retry_count",
        "recovery_eligible",
        "recovery_correct",
        "independent_acceptance",
        "reported_test_passed",
        "workflow_elapsed_s",
        "probe_supported",
        "infrastructure_error",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    atomic_text(destination / "results.csv", buffer.getvalue())
    atomic_text(destination / "summary.json", json.dumps(summary, indent=2, ensure_ascii=False))
    lines = [
        "# RelayForge measured local evaluation",
        "",
        f"Suite: {metadata.get('suite_id', 'unknown')}",
        "",
        "Generated from persisted raw observations. Counts include expected failures and safe unresolved outcomes; infrastructure errors are reported separately.",
        "",
        "| Mode | Executed / recorded | Duplicate effects | Unsafe retries | Recovery correct | Coding task success | False success |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    if metadata.get("configuration", {}).get("review_note"):
        lines[2:2] = ["> " + metadata["configuration"]["review_note"], ""]
    if any(row.get("case_schema_version", 1) < 2 for row in records):
        lines[2:2] = [
            "> Legacy development records retain their original observed-Run scope; they do not separate primary/Fork claims. Do not merge these pilot figures with the current primary-case comparison.",
            "",
        ]

    def rate(value):
        return (
            f"{value['numerator']}/{value['denominator']} ({100 * value['value']:.1f}%)"
            if value["value"] is not None
            else "undefined (0 denominator)"
        )

    for mode, row in summary["modes"].items():
        lines.append(
            f"| {mode} | {row['executed_cases']}/{row['recorded_cases']} | {row['duplicate_effect_count']} | {row['unsafe_retry_count']} | {rate(row['recovery_correctness'])} | {rate(row['coding_task_success'])} | {rate(row['false_success_rate'])} |"
        )
    lines += [
        "",
        "## Interpretation and boundaries",
        "",
        "A native-idempotent baseline can have zero duplicate effects. Request counts and actual committed effects are separate measurements; no improvement is assumed from the mode names.",
        "",
        "Coding task success and manual-review rate use the same primary coding Run per paired case in every mode. A Full-only Fork child is a separate supported capability probe, never a substitute task in the baseline comparison. Both primary and child code are independently tested. False-success auditing includes every reported success, including supported Fork children. Independently correct code alone does not turn an interrupted workflow into success.",
        "",
        "UNKNOWN does not assert failure or success. Cases without query/idempotency guarantees remain safe unresolved outcomes. Recovery correctness includes recoverable crash/loss cases; unsupported Fork probes and insufficient-protocol cases are excluded explicitly. Unsupported probes are not assigned arbitrary zero scores.",
        "",
        "Runtime overhead uses paired template/fault/seed observations with supported probes. It includes worker process startup, actual task tests and recovery/probes; it excludes environment setup, image build, task preparation and independent evaluator tests. Raw durations are retained; cache/CPU contention may affect local timing.",
        "",
        "These are deterministic local mock tasks, not random samples of real-world agent workloads. Small per-cell counts do not justify population claims or confidence intervals. Native approval controls retained across configurations are common safety controls, not evidence of a Full-only reliability gain.",
        "",
        "## Files",
        "",
        "results.jsonl preserves complete observations, process exits, protocol requests, test output, source/test hashes and probe evidence. results.csv is the compact comparison table. manifest.json records configuration, source fingerprints and actual environment versions. summary.json is computed, never prefilled.",
        "",
    ]
    atomic_text(destination / "report.md", "\n".join(lines))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    records = [
        json.loads(line)
        for line in args.raw.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metadata = json.loads((args.raw.parent / "manifest.json").read_text(encoding="utf-8"))
    summary = write_reports(args.output or args.raw.parent, records, metadata)
    print(
        json.dumps(
            {"records": summary["recorded_cases"], "output": str(args.output or args.raw.parent)}
        )
    )


if __name__ == "__main__":
    main()
