"""eval/tool_eval.py -- structural + regression checks for get_usage_example.

This is NOT a retrieval eval (see eval_real.py). It checks the generated
HCL itself, against the local index -- CHROMA_PATH must point at a
chroma_db built by pipeline.py:

    uv run eval/tool_eval.py

Checks per case:
  - the tool returns HCL without error
  - the HCL parses (balanced, well-formed)
  - every assigned variable exists in the module (no inventions)
  - every required variable (no default) is present
  - no empty-object placeholders (`[{}]`, the classic type-check failure)
  - must_include / must_exclude lists encoding reviewed judgments
    (e.g. website hosting must not suggest the deprecated `acl`)

Deliberately NOT asserted: judgments that need a reader, not a rule --
mutually exclusive modes (website_configuration vs the redirect-only
variable) and README-prose noise (privileged_principal_arns). The tool
surfaces those candidates; the caller trims them. See the tool docstring.
"""

import importlib.util
import re
import sys
from pathlib import Path

import hcl2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from store import get_collection, get_module_details_store  # noqa: E402
from usage import _is_required  # noqa: E402


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "terraform_parser",
        Path(__file__).resolve().parent.parent / "terraform-parser.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = [
    {
        "module": "cp-s3-bucket",
        "use_case": "host a static website",
        "must_include": [
            "website_configuration",
            "block_public_acls",
            "block_public_policy",
            "ignore_public_acls",
            "restrict_public_buckets",
            "s3_object_ownership",
        ],
        "must_exclude": [
            "acl",  # deprecated by AWS in favor of bucket policies
            # inputs-table neighbours, not website settings:
            "user_enabled",
            "access_key_enabled",
            "store_access_key_in_ssm",
        ],
    },
]


def _label(module_name: str) -> str:
    label = re.sub(r"[^a-zA-Z0-9_]", "_", module_name).strip("_")
    return re.sub(r"^terraform[_-]aws[_-]", "", label) or "example"


def check_case(case: dict, variables: list[dict], hcl: str) -> list[str]:
    """Return a list of failure messages; empty means the case passes."""
    var_names = {v["name"] for v in variables}
    required = {v["name"] for v in variables if _is_required(v)}
    try:
        parsed = hcl2.loads(hcl)
    except Exception as e:  # noqa: BLE001
        return [f"HCL does not parse: {e}"]
    # python-hcl2 keeps the quotes in block labels: '"cp_s3_bucket"'.
    want = _label(case["module"])
    body = None
    try:
        blocks = parsed["module"][0]
        for k, v in blocks.items():
            if k.strip('"') == want:
                body = v
                break
    except (KeyError, IndexError, TypeError, AttributeError):
        body = None
    if body is None:
        return ["module block not found in generated HCL"]
    # Skip hcl2's metadata keys and the source line.
    assigned = {k for k in body
                if k != "source" and not k.startswith("__")}

    failures = []
    invented = sorted(assigned - var_names)
    if invented:
        failures.append(f"invented variables: {invented}")
    missing = sorted(required - assigned)
    if missing:
        failures.append(f"missing required variables: {missing}")
    if "[{}]" in hcl:
        failures.append("empty-object placeholder [{}] present (fails type check)")
    for name in case.get("must_include", []):
        if name not in assigned:
            failures.append(f"expected '{name}' in snippet")
    for name in case.get("must_exclude", []):
        if name in assigned:
            failures.append(f"unexpected '{name}' in snippet")
    return failures


def main() -> int:
    tool = _load_tool()
    collection = get_collection()
    total_failures = 0
    ran = 0
    for case in CASES:
        details = get_module_details_store(collection, case["module"])
        if details is None:
            print(f"SKIP {case['module']!r}: not in the local index "
                  f"(index it with pipeline.py first)")
            continue
        res = tool.get_usage_example(case["module"], case["use_case"])
        if "error" in res:
            print(f"FAIL {case['module']}: {res['error']}")
            total_failures += 1
            continue
        ran += 1
        failures = check_case(case, details.get("variables", []), res["hcl"])
        if failures:
            print(f"FAIL {case['module']} ({case['use_case']!r}):")
            for f in failures:
                print(f"  - {f}")
            total_failures += len(failures)
        else:
            print(f"PASS {case['module']} ({case['use_case']!r})")
    if ran == 0:
        print("nothing ran -- all cases skipped")
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
