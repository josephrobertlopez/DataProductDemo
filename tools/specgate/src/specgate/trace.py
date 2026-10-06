"""Trace module: writes trace.json, mapping each AC to its `# implements:` and `# covers:` markers."""

import json
from pathlib import Path
from typing import Any

from specgate.l2_trace import scan_markers


def _portable(path: str) -> str:
    """Forward slashes on every OS.

    CI regenerates trace.json on Linux and fails on any byte difference, so a
    trace written on Windows with backslashes could never be committed green.
    """
    return Path(path).as_posix()


def gather_trace_data(
    ac_ids: set[str],
    src_dirs: list[str],
    test_dirs: list[str],
) -> dict[str, Any]:
    """
    Gather, per AC id, the `# implements:` and `# covers:` marker locations.

    Only marker locations are recorded; test results, coverage and mutation
    scores are reported by L3-L5, not stored in trace.json.

    Args:
        ac_ids: Set of known AC IDs
        src_dirs: List of source directories
        test_dirs: List of test directories

    Returns:
        Trace data dictionary with AC mappings
    """
    trace: dict[str, Any] = {}

    # Scan for markers in source and test files
    markers = scan_markers(src_dirs, test_dirs)

    # Build AC trace
    for ac_id in sorted(ac_ids):
        ac_trace: dict[str, Any] = {"id": ac_id}

        # Implementations
        impl_locs = []
        if ac_id in markers:
            for marker in markers[ac_id]:
                if marker.marker_type == "implements":
                    file_path = _portable(marker.file)
                    impl_locs.append({
                        "file": file_path,
                        "function": marker.funcname,
                        "line": marker.line,
                    })
        ac_trace["implements"] = impl_locs

        # Test functions that cover the AC
        covers_locs = []
        if ac_id in markers:
            for marker in markers[ac_id]:
                if marker.marker_type == "covers":
                    file_path = _portable(marker.file)
                    covers_locs.append({
                        "file": file_path,
                        "function": marker.funcname,
                        "line": marker.line,
                    })
        ac_trace["covers"] = covers_locs


        trace[ac_id] = ac_trace

    return trace


def write_trace_json(trace_data: dict[str, Any], output_path: str) -> None:
    """
    Write trace data to JSON file with sorted keys and no timestamps.

    Args:
        trace_data: Trace data dictionary
        output_path: Path to output JSON file
    """
    # Explicit LF and UTF-8 for the same byte-identical reason as _portable.
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(
            trace_data,
            f,
            sort_keys=True,
            indent=2,
        )
        # Add newline at end
        f.write('\n')


def generate_trace(
    ac_ids: set[str],
    src_dirs: list[str],
    test_dirs: list[str],
    output_path: str = "trace.json",
) -> None:
    """
    Generate and write trace.json file.

    Args:
        ac_ids: Set of known AC IDs
        src_dirs: List of source directories
        test_dirs: List of test directories
        output_path: Path to write trace.json
    """
    trace_data = gather_trace_data(
        ac_ids,
        src_dirs,
        test_dirs,
    )
    write_trace_json(trace_data, output_path)
