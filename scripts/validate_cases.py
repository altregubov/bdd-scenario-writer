#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pydantic>=2.5.0",
#     "pyyaml>=6.0.1",
# ]
# ///
"""
Strict Pydantic v2 Validator & OpenAPI Cross-Verification Script.
Ensures test cases adhere to schema and contain zero hallucinated endpoints.
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

try:
    import yaml
    from pydantic import (
        BaseModel,
        Field,
        ValidationError,
        field_validator,
        model_validator,
    )
except ImportError:
    print(
        "❌ Missing runtime dependencies. Execute via 'uv run' or install dependencies.",
        file=sys.stderr,
    )
    sys.exit(1)

# ==============================================================================
# Pydantic v2 Data Models
# ==============================================================================

class Step(BaseModel):
    step: int = Field(gt=0, description="Step sequence number (1-based)")
    action: str = Field(min_length=5, description="Action description")
    expected_status: int = Field(
        ge=100, le=599, description="Expected HTTP response status code"
    )
    extract: Optional[Dict[str, str]] = Field(
        default=None, description="Context variables to capture from response"
    )


class Coverage(BaseModel):
    endpoints: List[str] = Field(
        min_length=1,
        description="List of endpoints formatted as 'METHOD /path'",
    )
    requirement_ref: Optional[str] = Field(
        default=None, description="Link to requirement clause in PRD"
    )

    @field_validator("endpoints")
    @classmethod
    def validate_endpoint_format(cls, v: List[str]) -> List[str]:
        pattern = r"^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+\/[\w\-\.\/\{\}]+$"
        for ep in v:
            if not re.match(pattern, ep.strip()):
                raise ValueError(
                    f"Invalid endpoint format '{ep}'. Expected: 'METHOD /path' (e.g., 'POST /v1/auth/login')"
                )
        return [ep.strip() for ep in v]


class TestCase(BaseModel):
    id: str = Field(
        pattern=r"^TC-[A-Z0-9]+-\d{3}$",
        description="ID format: TC-<MODULE>-<INDEX:03d>",
    )
    title: str = Field(min_length=8, description="Test case description")
    type: Literal["positive", "negative", "boundary", "security", "rbac"]
    priority: Literal["P0", "P1", "P2"]
    coverage: Coverage
    preconditions: List[str] = Field(
        min_length=1, description="Prerequisites for test setup"
    )
    test_data: Optional[Dict[str, Any]] = None
    steps: List[Step] = Field(min_length=1, description="Scenario steps")
    expected_results: List[str] = Field(
        min_length=1, description="Assertions and side effects"
    )


class ModuleHeader(BaseModel):
    id: str = Field(pattern=r"^[A-Z0-9]+$", description="Module uppercase ID")
    domain: str = Field(min_length=3, description="Domain title")
    target_spec: Optional[str] = None
    product_spec: Optional[str] = None


class ModuleDefaults(BaseModel):
    preconditions: Optional[List[str]] = Field(default_factory=list)


class TestSuiteFile(BaseModel):
    module: ModuleHeader
    module_defaults: Optional[ModuleDefaults] = None
    test_cases: List[TestCase] = Field(
        min_length=1, description="Module test cases"
    )

    @model_validator(mode="after")
    def verify_module_id_matches_cases(self) -> "TestSuiteFile":
        mod_prefix = f"TC-{self.module.id}-"
        for tc in self.test_cases:
            if not tc.id.startswith(mod_prefix):
                raise ValueError(
                    f"Test ID '{tc.id}' does not match module '{self.module.id}'. "
                    f"Expected prefix: '{mod_prefix}'"
                )
        return self


# ==============================================================================
# OpenAPI Inspector & Validator Core
# ==============================================================================

def load_openapi_endpoints(openapi_path: Path) -> Set[str]:
    if not openapi_path.exists():
        print(
            f"⚠️  [WARN] Spec file '{openapi_path}' not found. Skipping contract verification.",
            file=sys.stderr,
        )
        return set()

    with open(openapi_path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}

    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    valid_endpoints = set()

    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in path_item.keys():
            if method.lower() in http_methods:
                valid_endpoints.add(f"{method.upper()} {path}")

    return valid_endpoints


def run_validations(
    cases_dir: Path = Path("specs/test_cases"),
    openapi_path: Path = Path("specs/openapi.yaml"),
) -> bool:
    print("🔍 Executing strict Pydantic v2 validation & OpenAPI cross-check...")
    openapi_endpoints = load_openapi_endpoints(openapi_path)
    if openapi_endpoints:
        print(f"📘 Loaded {len(openapi_endpoints)} valid endpoints from OpenAPI.")

    errors: List[str] = []
    seen_ids: Dict[str, str] = {}
    valid_case_count = 0

    yaml_files = list(cases_dir.rglob("*.yaml"))
    if not yaml_files:
        errors.append(f"Directory '{cases_dir}' is empty or contains no YAML files.")

    for file_path in yaml_files:
        if file_path.name.startswith("_"):
            continue

        rel_path = file_path.as_posix()
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f)
        except Exception as e:
            errors.append(f"[{rel_path}] YAML syntax error: {e}")
            continue

        # 1. Pydantic schema validation
        try:
            suite = TestSuiteFile.model_validate(content)
        except ValidationError as val_err:
            for err in val_err.errors():
                loc = " -> ".join(str(p) for p in err["loc"])
                msg = err["msg"]
                errors.append(f"[{rel_path}] Schema error at `{loc}`: {msg}")
            continue

        # 2. Duplicate ID detection & OpenAPI validation
        for tc in suite.test_cases:
            if tc.id in seen_ids:
                errors.append(
                    f"[{rel_path}] Duplicate test ID '{tc.id}'. Already registered in: {seen_ids[tc.id]}"
                )
            else:
                seen_ids[tc.id] = rel_path
                valid_case_count += 1

            # 3. Cross-validate against real OpenAPI spec
            if openapi_endpoints:
                for ep in tc.coverage.endpoints:
                    if ep not in openapi_endpoints:
                        errors.append(
                            f"[{rel_path} | {tc.id}] Endpoint hallucination: '{ep}' does not exist in {openapi_path}"
                        )

    if errors:
        print(f"\n❌ VALIDATION FAILED: {len(errors)} error(s) found:\n", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        return False

    print(
        f"\n✅ All test suites valid! Checked {valid_case_count} test cases across {len(yaml_files)} file(s)."
    )
    return True


if __name__ == "__main__":
    success = run_validations()
    sys.exit(0 if success else 1)
