"""Parsing: Terraform module directories -> structured JSON."""

import hcl2
import json
from pathlib import Path


def _clean(value):
    """python-hcl2 8.x leaves literal quote characters on string
    values and block names. Strip them so we get plain Python strings."""
    if isinstance(value, str) and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _clean_type(value):
    """Clean a variable type string. Besides _clean()'s quote stripping,
    legacy `${...}` interpolation syntax (e.g. "${map(string)}") is
    unwrapped to the inner type name."""
    value = _clean(value)
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return value[2:-1]
    return value


def parse_variables(module_path: str) -> list[dict]:
    var_file = Path(module_path) / "variables.tf"
    if not var_file.exists():
        return []

    with open(var_file, "r") as f:
        parsed = hcl2.load(f)

    variables = []
    for block in parsed.get("variable", []):
        for raw_name, attrs in block.items():
            variables.append({
                "name": _clean(raw_name),
                "type": _clean_type(attrs.get("type")),
                "default": _clean(attrs.get("default")),
                # `default = null` is the modules' idiom for "optional, omit
                # to leave unset" -- only a missing default is truly required.
                # hcl2 keeps the key with value None for explicit nulls.
                "has_default": "default" in attrs,
                "description": _clean(attrs.get("description")),
            })
    return variables


def parse_outputs(module_path: str) -> list[dict]:
    out_file = Path(module_path) / "outputs.tf"
    if not out_file.exists():
        return []

    with open(out_file, "r") as f:
        parsed = hcl2.load(f)

    outputs = []
    for block in parsed.get("output", []):
        for raw_name, attrs in block.items():
            outputs.append({
                "name": _clean(raw_name),
                "description": _clean(attrs.get("description")),
            })
    return outputs


def parse_resource_types(module_path: str) -> list[str]:
    """Extract just the resource type names (e.g. 'aws_s3_bucket') from
    every *.tf file in the module root -- not the full resource bodies,
    which are noise for search. Terraform treats all .tf files in a
    directory as one configuration; main.tf is only a naming convention,
    and real modules (e.g. jameswoolfenden's) spread resources across
    files like aws_db_instance.instance.tf."""
    resource_types: list[str] = []
    for tf_file in sorted(Path(module_path).glob("*.tf")):
        try:
            with open(tf_file, "r") as f:
                parsed = hcl2.load(f)
        except Exception:
            continue
        for block in parsed.get("resource", []):
            for raw_type in block:
                cleaned = _clean(raw_type)
                if cleaned not in resource_types:
                    resource_types.append(cleaned)
    return resource_types


def parse_readme(module_path: str, max_chars: int = 12000) -> str:
    """Return the module's README text as-is (human-written prose about
    intent -- no parsing needed). Empty string if there isn't one."""
    for name in ("README.md", "readme.md"):
        readme_file = Path(module_path) / name
        if readme_file.exists():
            return readme_file.read_text(encoding="utf-8")[:max_chars]
    return ""




def parse_module(module_path: str) -> dict:
    """Parse a module directory into structured JSON (name, variables,
    outputs, resource types, README). Pure function -- the MCP tool
    `parse_module` in server.py wraps this."""
    return {
        "module_name": Path(module_path).name,
        "variables": parse_variables(module_path),
        "outputs": parse_outputs(module_path),
        "resource_types": parse_resource_types(module_path),
        "readme": parse_readme(module_path),
    }
