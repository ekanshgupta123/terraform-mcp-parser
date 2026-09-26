"""Summarizer: parsed module JSON -> plain-English summary via an LLM.

This is stage 2 of the ingestion pipeline. It runs ONCE per module at index
time (and again only when a module changes) -- never at query time. The
structured summary it produces is what gets embedded, because natural
language ("private encrypted database") matches search queries far better
than raw variable dumps do.

Requires ANTHROPIC_API_KEY in the environment (see .env.example).
"""

from email.mime import text
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
  "typical_use_cases": ["2-3 one-sentence scenarios"]
}
Rules:
- Never just restate variable names as the summary.
- Prefer describing capability over implementation
  ("provisions an encrypted private database" not
  "creates aws_db_instance with storage_encrypted=true").
- If the README already states intent clearly, lean on it rather than
  re-deriving from raw resource types.
- Cover the resource type's canonical real-world uses among the use
  cases (e.g. an S3 bucket module: static website hosting, log
  archival, Terraform state storage; a security group: firewall rules
  for EC2/RDS). Only include uses supported by the README, variables,
  or resource types -- don't invent any."""

# Variables whose descriptions mention these tend to describe what the
# module is *for* (capability), rather than generic sizing/naming knobs.
CAPABILITY_KEYWORDS = [
    "encrypt", "public", "private", "polic", "secur", "block",
    "replicat", "lifecycle", "log", "version", "access", "lock",
    "website", "host",
]


def select_key_variables(variables: list[dict], max_vars: int = 15) -> list[dict]:
    """Trim the full variable list down to the ones that shape the module's
    identity for summarization. Falls back to the first N variables if too
    few match, so the summarizer never gets an empty list."""
    key_vars = [
        v for v in variables
        if v.get("description")
        and any(kw in v["description"].lower() for kw in CAPABILITY_KEYWORDS)
    ]
    if len(key_vars) < 5:
        key_vars = variables[:max_vars]
    return key_vars[:max_vars]


def build_summary_input(parsed_module: dict) -> dict:
    """Build the trimmed input dict that actually gets sent to the LLM."""
    return {
        "module_name": parsed_module["module_name"],
        "key_variables": [
            {"name": v["name"], "description": v.get("description", "")}
            for v in select_key_variables(parsed_module.get("variables", []))
        ],
        "outputs": parsed_module.get("outputs", []),
        "resource_types": parsed_module.get("resource_types", []),
        "readme": parsed_module.get("readme", ""),
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
        max_tokens=1000,
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
