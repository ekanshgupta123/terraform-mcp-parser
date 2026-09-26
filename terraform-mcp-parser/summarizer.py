"""Summarizer: parsed module JSON -> plain-English summary via an LLM.

This is stage 2 of the ingestion pipeline. It runs ONCE per module at index
time (and again only when a module changes) -- never at query time. The
structured summary it produces is what gets embedded, because natural
language ("private encrypted database") matches search queries far better
than raw variable dumps do.

Requires ANTHROPIC_API_KEY in the environment (see .env.example).
"""

import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are documenting a Terraform module for a semantic
search index used by AI agents. Given the module's variables, outputs,
resource types, and README, produce a structured summary a person could
use to decide whether this module fits their infrastructure need --
without reading the code.

Output strict JSON, no markdown fences, no preamble:
{
  "summary": "1-2 sentences, plain English, no Terraform jargon.
    What does this provision and why would someone use it?",
  "key_inputs": ["variable_name: what it controls, in plain English"],
  "key_outputs": ["output_name: what it exposes"],
  "typical_use_cases": ["2-3 one-sentence scenarios"],
  "distinguishing_features": ["1-3 short phrases: what this module does
    that a generic module of the same resource type does NOT"]
}
Rules:
- Never just restate variable names as the summary.
- Prefer describing capability over implementation
  ("provisions an encrypted private database" not
  "creates aws_db_instance with storage_encrypted=true").
- If the README already states intent clearly, lean on it rather than
  re-deriving from raw resource types.
- Use cases must come from what THIS module is built for, as shown by
  its README, variables (including defaults), and resource types. Do
  not add generic uses that any module of the same resource type could
  claim, and do not name other AWS services as consumers unless the
  module wires them up itself. If the module is specialized (e.g. a
  log-only bucket), say so in the summary.
- distinguishing_features should name concrete extras such as
  companion resources (an Elastic IP, a CloudFront origin access
  identity, an SQS dead letter queue) or notable defaults (logs expire
  after 90 days). Use an empty list if there are none."""

# Variables whose descriptions mention these tend to describe what the
# module is *for* (capability), rather than generic sizing/naming knobs.
CAPABILITY_KEYWORDS = [
    "encrypt", "public", "private", "polic", "secur", "block",
    "replicat", "lifecycle", "log", "version", "access", "lock",
    "website", "host", "retention", "expir", "days", "eip", "elastic",
    "listener", "https", "dlq", "dead letter", "backup", "engine",
]


def _resource_tokens(resource_types: list[str]) -> set[str]:
    """'aws_eip' -> {'eip'}, 'aws_s3_bucket_lifecycle_configuration' ->
    {'lifecycle'}: short tokens naming each companion resource."""
    generic = {"aws", "s3", "bucket", "configuration", "policy", "rule",
               "attachment", "association", "vpc", "security", "group"}
    return {t for rt in resource_types for t in rt.split("_")
            if len(t) > 2 and t not in generic}


def select_key_variables(variables: list[dict], resource_types: list[str] | None = None,
                         max_vars: int = 20,
                         send_all_under: int = 40) -> list[dict]:
    """Trim the full variable list down to the ones that shape the module's
    identity for summarization.

    Priority 1: variables whose NAME matches a companion resource type
    (create_eip -> aws_eip). These mark distinguishing features, and on
    big modules they used to get crowded out by the keyword matches.
    Priority 2: capability keywords in the name or description.
    Falls back to the first N variables if too few match.

    Small modules (<= send_all_under variables) skip the filter: their
    full list is cheap to send, and filtering only risks dropping the one
    variable that matters (trussworks-logs' s3_log_bucket_retention,
    crowded out by a dozen allow_* vars that all mention "log")."""
    if len(variables) <= send_all_under:
        return variables
    # Only rare tokens mark a companion feature: 'eip' hits 1-3 variables,
    # while the primary resource's token ('instance') hits dozens.
    res_tokens = {tok for tok in _resource_tokens(resource_types or [])
                  if 0 < sum(tok in v["name"].lower().split("_")
                             for v in variables) <= 3}
    tier1, tier2 = [], []
    for v in variables:
        name = v["name"].lower()
        text = name + " " + (v.get("description") or "").lower()
        if any(tok in name.split("_") for tok in res_tokens):
            tier1.append(v)
        elif any(kw in text for kw in CAPABILITY_KEYWORDS):
            tier2.append(v)
    key_vars = tier1 + tier2
    if len(key_vars) < max_vars:
        # Top up from the front of the full list so the summarizer never
        # gets a near-empty input; tier matches keep their priority.
        matched = {v["name"] for v in key_vars}
        key_vars += [v for v in variables
                     if v["name"] not in matched][:max_vars - len(key_vars)]
    return key_vars[:max_vars]


def clean_readme(readme: str, max_chars: int = 4000) -> str:
    """Drop fenced code blocks, badge/image lines and HTML comments, then
    truncate. On big modules the first 4000 raw chars are mostly HCL usage
    examples and badges, which pushes the prose about capabilities out."""
    readme = re.sub(r"```.*?```", "", readme, flags=re.DOTALL)
    readme = re.sub(r"<!--.*?-->", "", readme, flags=re.DOTALL)
    lines = [ln for ln in readme.splitlines()
             if not re.match(r"^\s*(\[!\[|!\[|<img|<a href)", ln)]
    readme = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return readme[:max_chars]


def build_summary_input(parsed_module: dict) -> dict:
    """Build the trimmed input dict that actually gets sent to the LLM."""
    return {
        "module_name": parsed_module["module_name"],
        "key_variables": [
            {"name": v["name"], "description": v.get("description", ""),
             "default": v.get("default")}
            for v in select_key_variables(parsed_module.get("variables", []),
                                          parsed_module.get("resource_types", []))
        ],
        "outputs": parsed_module.get("outputs", []),
        "resource_types": parsed_module.get("resource_types", []),
        "readme": clean_readme(parsed_module.get("readme", "")),
    }


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if the model added them anyway."""
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.DOTALL)
    return match.group(1) if match else text


def summarize_module(parsed_module: dict, model: str | None = None) -> dict:
    """Summarize one parsed module. Returns the structured summary dict."""
    model = model or os.environ.get("SUMMARIZER_MODEL", "claude-sonnet-5")
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": json.dumps(build_summary_input(parsed_module), indent=2),
        }],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(_strip_fences(text))


if __name__ == "__main__":
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "terraform_parser", "terraform-parser.py")
    tp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tp)

    module_path = sys.argv[1] if len(sys.argv) > 1 else "../sample-modules/terraform-aws-s3-bucket"
    parsed = tp.parse_module(module_path)
    print(json.dumps(summarize_module(parsed), indent=2))
