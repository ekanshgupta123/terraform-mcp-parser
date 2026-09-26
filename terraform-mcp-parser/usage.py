"""usage.py: deterministic Terraform usage-example generator.

Builds a paste-ready `module` block from a module's PARSED variables --
no LLM, no API calls. Names, types, defaults, and descriptions come
straight from variables.tf, so the snippet can't invent variables,
truncate output names, or hallucinate placeholders (the failure mode of
asking an LLM to freestyle HCL from a summary).

Selection: required variables (no default) are always included. When
the caller passes a use_case ("host a static website"), optional
variables whose name/description overlap the use case are added too.
Everything else keeps its default and stays out of the snippet.
"""

import json
import re

# Tokens too generic to signal relevance on their own.
_STOPWORDS = {
    "a", "an", "the", "to", "for", "of", "and", "or", "with", "my",
    "i", "want", "need", "use", "using", "me", "please", "that",
    "this", "it", "in", "on", "is", "are", "be",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower())) - _STOPWORDS


def _placeholder(vartype: str | None) -> str:
    """A valid-HCL, obviously-fill-me-in value for the given type."""
    t = (vartype or "").strip().lower()
    if t == "string":
        return '"your-value-here"'
    if t == "number":
        return "0"
    if t == "bool":
        return "false"
    if t.startswith(("list(", "set(", "tuple(")):
        return "[]"
    if t.startswith(("map(", "object(")):
        return "{}"
    # "any" or anything unrecognised: a string works almost everywhere
    # a scalar is expected, and is clearly a placeholder.
    return '"your-value-here"'


def _is_required(var: dict) -> bool:
    # Only a missing default is truly required: `default = null` is the
    # modules' idiom for optional. Older stored indexes lack has_default;
    # fall back to the default value there.
    if "has_default" in var:
        return not var["has_default"]
    return var.get("default") is None


def _relevance(var: dict, use_case_tokens: set[str]) -> int:
    if not use_case_tokens:
        return 0
    hay = _tokens(var.get("name", "") + " " + (var.get("description") or ""))
    return len(hay & use_case_tokens)


def extract_source(readme: str | None) -> str | None:
    """Best-effort registry source from the README's own usage example
    (e.g. source = "terraform-aws-modules/s3-bucket/aws")."""
    m = re.search(r'source\s*=\s*"([^"\n]+)"', readme or "")
    return m.group(1) if m else None


def _fmt_default(value) -> str:
    if isinstance(value, str):
        return value if len(value) <= 40 else value[:37] + "..."
    try:
        s = json.dumps(value)
    except (TypeError, ValueError):
        s = str(value)
    return s if len(s) <= 40 else s[:37] + "..."


def select_variables(variables: list[dict], use_case: str = "",
                     max_optional: int = 8) -> tuple[list[dict], list[dict]]:
    """Split into (required, relevant-optional) variables for the snippet."""
    required = [v for v in variables if _is_required(v)]
    optional = [v for v in variables if not _is_required(v)]
    if not use_case:
        return required, []
    use_case_tokens = _tokens(use_case)
    scored = sorted(
        (( _relevance(v, use_case_tokens), v) for v in optional),
        key=lambda p: p[0], reverse=True,
    )
    relevant = [v for score, v in scored if score > 0][:max_optional]
    return required, relevant


def build_usage_example(parsed: dict, use_case: str = "",
                        summary: str = "") -> str:
    """Render the HCL snippet for one parsed module dict."""
    module_name = parsed.get("module_name", "module")
    label = re.sub(r"[^a-zA-Z0-9_]", "_", module_name).strip("_") or "example"
    # "terraform-aws-s3-bucket" -> "s3_bucket": the snippet reads better
    # without the registry-namespace prefix.
    label = re.sub(r"^terraform[_-]aws[_-]", "", label)
    variables = parsed.get("variables", []) or []
    outputs = parsed.get("outputs", []) or []
    readme = parsed.get("readme", "")

    required, relevant = select_variables(variables, use_case)

    lines: list[str] = []
    if summary:
        first = summary.strip().split("\n")[0][:120]
        lines.append(f"# {first}")
    lines.append(f'module "{label}" {{')

    source = extract_source(readme)
    if source:
        lines.append(f'  source = "{source}"')
    else:
        lines.append('  source = "<MODULE_SOURCE>"  # TODO: set the module source')

    if required or relevant:
        lines.append("")
    name_w = max((len(v["name"]) for v in required + relevant), default=0)
    for v in required:
        pad = " " * (name_w - len(v["name"]))
        lines.append(f'  {v["name"]}{pad} = {_placeholder(v.get("type"))}  # TODO: required, no default')
    if required and relevant:
        lines.append("")
    if relevant:
        if use_case:
            lines.append(f'  # Relevant to "{use_case}":')
        for v in relevant:
            pad = " " * (name_w - len(v["name"]))
            lines.append(f'  {v["name"]}{pad} = {_placeholder(v.get("type"))}'
                         f'  # default: {_fmt_default(v.get("default"))}'
                         f' — change to enable')
    lines.append("}")

    if outputs:
        use_case_tokens = _tokens(use_case)
        scored = sorted(
            ((_relevance(o, use_case_tokens), o) for o in outputs),
            key=lambda p: p[0], reverse=True,
        )
        top = [o for _, o in scored[:10]]
        lines.append("")
        lines.append("# Key outputs you can reference:")
        for o in top:
            desc = (o.get("description") or "").strip().split("\n")[0][:80]
            tail = f" - {desc}" if desc else ""
            lines.append(f"#   module.{label}.{o['name']}{tail}")

    return "\n".join(lines) + "\n"
