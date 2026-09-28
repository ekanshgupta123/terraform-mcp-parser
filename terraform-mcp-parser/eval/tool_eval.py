"""eval/tool_eval.py -- structural + regression checks for get_usage_example.

This is NOT a retrieval eval (see eval_real.py). It checks the generated
HCL itself, against the local index -- CHROMA_PATH must point at a
chroma_db built by terraform-mcp-index:

    uv run eval/tool_eval.py

Checks per case:
  - the tool returns HCL without error
  - the HCL parses (balanced, well-formed)
  - every assigned variable exists in the module (no inventions)
  - every required variable (no default) is present
  - no empty-object placeholders (`[{}]`, the classic type-check failure)
  - must_include / must_exclude lists encoding reviewed judgments
    (e.g. website hosting must not suggest the deprecated `acl`)
  - must_include_any: at least one of each group (for judgments that
    hold across module versions with different variable names)
  - expect_notes: the rendered line for a variable carries a warning
    (e.g. the public-read-policy note on source_policy_documents)

Deliberately NOT asserted: judgments that need a reader, not a rule --
README-prose noise (privileged_principal_arns) and which remaining bools
to flip. The tool surfaces those candidates; the caller trims them. The
website hosting-vs-redirect mode pair IS resolved deterministically from
the use case and asserted below; other undeclared mode conflicts remain
caller judgment. See the tool docstring.
"""

import re
import sys
from pathlib import Path

import hcl2

from terraform_mcp_parser.store import get_collection, get_module_details_store
from terraform_mcp_parser.usage import _is_required


def _load_tool():
    from terraform_mcp_parser import server as mod
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
            # the 403 trap: a website needs a public read policy attached
            "source_policy_documents",
        ],
        "must_exclude": [
            "acl",  # deprecated by AWS in favor of bucket policies
            # redirect-only mode, not hosting:
            "website_redirect_all_requests_to",
            # inputs-table neighbours, not website settings:
            "user_enabled",
            "access_key_enabled",
            "store_access_key_in_ssm",
        ],
        "expect_notes": {
            "source_policy_documents": "403",
            "block_public_policy": "set to false",
            "restrict_public_buckets": "set to false",
            "website_configuration": "hosting mode",
        },
    },
    {
        # The flip side of the website case: redirect mode must surface
        # the redirect variable and drop the hosting config.
        "module": "cp-s3-bucket",
        "use_case": "redirect all website requests to example.com",
        "must_include": ["website_redirect_all_requests_to"],
        "must_exclude": ["website_configuration"],
        "expect_notes": {
            "website_redirect_all_requests_to": "redirect-only mode",
        },
    },
    {
        # Non-S3 generality check: the tool must surface an ingress-rules
        # input for a firewall use case. v5 names it
        # ingress_with_cidr_blocks, v6 ingress_rules -- the judgment is
        # "an ingress input is surfaced", not the exact name.
        "module": "tam-security-group",
        "use_case": "firewall rules for EC2 instances",
        "must_include_any": [
            ["ingress_rules", "ingress_with_cidr_blocks"],
        ],
        "must_exclude": [],
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
    # Skip hcl2's metadata keys, the source line, and the resolved
    # registry version pin (not a module variable).
    assigned = {k for k in body
                if k not in ("source", "version") and not k.startswith("__")}

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
    for group in case.get("must_include_any", []):
        if not any(name in assigned for name in group):
            failures.append(f"expected one of {group} in snippet")
    for name in case.get("must_exclude", []):
        if name in assigned:
            failures.append(f"unexpected '{name}' in snippet")
    for name, substr in case.get("expect_notes", {}).items():
        lines = hcl.splitlines()
        start = next((i for i, ln in enumerate(lines)
                      if re.match(rf"\s*{re.escape(name)}\s*=", ln)), None)
        if start is None:
            failures.append(f"expected a line assigning '{name}' for note check")
            continue
        # The rendered value may span lines (type-driven objects); the
        # note lands on the value's last line, so scan the whole value:
        # the assignment line, deeper-indented continuation lines, and
        # the closing-bracket line.
        span = [lines[start]]
        for ln in lines[start + 1:]:
            if not ln.strip():
                break
            indent = len(ln) - len(ln.lstrip())
            if indent > 2:
                span.append(ln)
                continue
            if re.match(r"\s*[\]}]", ln):
                span.append(ln)
            break
        if substr not in "\n".join(span):
            failures.append(f"expected note containing {substr!r} on '{name}'")
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
                  f"(index it with terraform-mcp-index first)")
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
