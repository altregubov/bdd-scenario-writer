#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6.0.1",
# ]
# ///
"""
Inventory and ID Tracking Script for qa-spec-analyst skill.
Extracts module states, consumed test IDs, and covered endpoints.
"""
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "❌ Missing dependency 'pyyaml'. Execute via 'uv run' or install dependencies.",
        file=sys.stderr,
    )
    sys.exit(1)


def analyze_inventory(base_dir: Path = Path("specs/test_cases")) -> dict:
    inventory = {
        "total_cases": 0,
        "modules": {},
        "all_used_ids": [],
        "covered_endpoints": [],
    }

    if not base_dir.exists():
        return inventory

    for file_path in base_dir.rglob("*.yaml"):
        if file_path.name.startswith("_"):
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"[WARN] Failed to read {file_path}: {e}", file=sys.stderr)
            continue

        module_info = data.get("module", {})
        module_id = module_info.get("id", "GEN")
        cases = data.get("test_cases", [])

        if module_id not in inventory["modules"]:
            inventory["modules"][module_id] = {
                "domain": module_info.get("domain", "General"),
                "files": [],
                "case_count": 0,
                "highest_index": 0,
            }

        rel_path = str(file_path.as_posix())
        if rel_path not in inventory["modules"][module_id]["files"]:
            inventory["modules"][module_id]["files"].append(rel_path)

        for case in cases:
            inventory["total_cases"] += 1
            inventory["modules"][module_id]["case_count"] += 1
            cid = case.get("id", "")

            if cid:
                inventory["all_used_ids"].append(cid)
                match = re.search(r"-(\d+)$", cid)
                if match:
                    idx = int(match.group(1))
                    if idx > inventory["modules"][module_id]["highest_index"]:
                        inventory["modules"][module_id]["highest_index"] = idx

            endpoints = case.get("coverage", {}).get("endpoints", [])
            for ep in endpoints:
                if ep not in inventory["covered_endpoints"]:
                    inventory["covered_endpoints"].append(ep)

    for mod_id, meta in inventory["modules"].items():
        next_idx = meta["highest_index"] + 1
        meta["next_suggested_id"] = f"TC-{mod_id}-{next_idx:03d}"

    return inventory


if __name__ == "__main__":
    result = analyze_inventory()
    print(json.dumps(result, indent=2, ensure_ascii=False))
