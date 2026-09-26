"""usage.py: deterministic Terraform usage-example generator.

Builds a paste-ready `module` block from a module's PARSED variables --
no LLM, no API calls. Names, types, defaults, and descriptions come
straight from variables.tf, so the snippet can't invent variables,
truncate output names, or hallucinate placeholders (the failure mode of
asking an LLM to freestyle HCL from a summary).

Two things make the snippet more than a skeleton:

1. Type-driven values. A `list(object({...}))` variable is rendered as
   an example object built from its type constraint -- field names,
   nesting, and shapes included -- instead of a bare `[]`.
2. Related variables. Beyond required vars and direct keyword matches,
   the tool pulls in variables the use case depends on: ones named in
   the module's summary, and ones mentioned near use-case keywords in
   the README (e.g. the S3 public-access-block settings that a static
   website needs alongside `website_configuration`).
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


# ---------------------------------------------------------------------------
# Terraform type-constraint parsing.
#
# Handles the subset modules actually use: string/number/bool/any,
# list(...)/set(...)/map(...), object({...}), tuple([...]),
# optional(...) with or without a default. Anything unrecognised falls
# back to a scalar placeholder (see _example_for_type).
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"|\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_-]*|[(){}\[\],=]'
)


def _parse_type(s: str):
    """Parse a type-constraint string into a node tree.

    Nodes: ("prim", name), ("literal", tok), ("list"|"set"|"map", elem),
    ("object", [(name, node)]), ("tuple", [nodes]), ("optional", node).
    """
    toks = [t for t in _TOKEN_RE.findall(s) if t.strip()]
    if not toks:
        raise ValueError("empty type")
    i = 0

    def peek():
        return toks[i] if i < len(toks) else None

    def eat(expected=None):
        nonlocal i
        tok = toks[i]
        i += 1
        if expected is not None and tok != expected:
            raise ValueError(f"expected {expected!r}, got {tok!r}")
        return tok

    def skip_balanced():
        """Skip tokens up to and including the matching close paren."""
        depth = 1
        while depth:
            t = eat()
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1

    def parse():
        tok = eat()
        if (tok.startswith('"') or tok in ("true", "false", "null")
                or re.fullmatch(r"\d+(\.\d+)?", tok)):
            return ("literal", tok)
        name = tok
        if peek() == "(":
            eat("(")
            if name in ("list", "set", "map"):
                elem = parse()
                eat(")")
                return (name, elem)
            if name == "optional":
                elem = parse()
                if peek() == ",":
                    eat(",")
                    skip_balanced()  # the default value; not needed
                else:
                    eat(")")
                return ("optional", elem)
            if name == "tuple":
                eat("[")
                elems = []
                while peek() != "]":
                    elems.append(parse())
                    if peek() == ",":
                        eat(",")
                eat("]")
                eat(")")
                return ("tuple", elems)
            if name == "object":
                # object({...}) -- parens around the brace block.
                eat("{")
                fields = []
                while peek() != "}":
                    fname = eat()
                    eat("=")
                    fields.append((fname, parse()))
                    if peek() == ",":
                        eat(",")
                eat("}")
                eat(")")
                return ("object", fields)
            # Unknown constructor: skip it, treat as opaque.
            skip_balanced()
            return ("prim", "any")
        if peek() == "{":
            eat("{")
            fields = []
            while peek() != "}":
                fname = eat()
                eat("=")
                fields.append((fname, parse()))
                if peek() == ",":
                    eat(",")
            eat("}")
            return ("object", fields)
        return ("prim", name)

    node = parse()
    return node


def _render_value(node, level: int, depth: int = 4) -> str:
    """Render an example HCL value for a type node. `level` is the indent
    level of the line the value starts on (2 spaces per level).

    `depth` budgets object/map expansions only: list/set/tuple are
    transparent wrappers, so `list(object({a = object({...})}))` expands
    fully instead of collapsing the inner object to `{}` two levels
    down. A spent budget degrades gracefully -- deep lists become `[]`
    (always type-valid), deep objects become `{}`.
    """
    kind = node[0]
    if kind == "prim":
        return {"string": '"your-value-here"', "number": "0",
                "bool": "false"}.get(node[1], '"your-value-here"')
    if kind == "literal":
        return node[1]
    if kind == "optional":
        return _render_value(node[1], level, depth)
    pad = "  " * level
    if kind in ("list", "set", "tuple"):
        if depth <= 0:
            return "[]"
        if kind == "tuple":
            inner = ", ".join(_render_value(e, level, depth)
                              for e in node[1])
            return "[" + inner + "]"
        elem = node[1]
        inner = _render_value(elem, level, depth)
        if elem[0] == "object":
            # [{ ... }] on the same open line, like hand-written HCL.
            return "[" + inner + "]"
        if "\n" not in inner:
            return "[" + inner + "]"
        indented = "\n".join(pad + "  " + ln if ln.strip() else ln
                             for ln in inner.split("\n"))
        return "[\n" + indented + "\n" + pad + "]"
    if kind == "map":
        if depth <= 0:
            return "{}"
        inner = _render_value(node[1], level + 1, depth)
        return '{\n' + pad + '  "example-key" = ' + inner + "\n" + pad + "}"
    if kind == "object":
        if depth <= 0 or not node[1]:
            return "{}"
        fields = node[1]
        w = max(len(n) for n, _ in fields)
        lines = []
        for n, t in fields:
            v = _render_value(t, level + 1, depth - 1)
            lines.append(pad + "  " + n.ljust(w) + " = " + v)
        return "{\n" + "\n".join(lines) + "\n" + pad + "}"
    return '"your-value-here"'


def _example_for_type(vartype: str | None) -> str:
    """Best-effort example value for a type string; scalar placeholder
    fallback when the type is missing or unparseable."""
    if not vartype or not str(vartype).strip():
        return '"your-value-here"'
    try:
        return _render_value(_parse_type(str(vartype)), 1)
    except Exception:
        t = str(vartype).strip().lower()
        if t == "number":
            return "0"
        if t == "bool":
            return "false"
        if t.startswith(("list(", "set(", "tuple(")):
            return "[]"
        if t.startswith(("map(", "object(")):
            return "{}"
        return '"your-value-here"'


# ---------------------------------------------------------------------------
# Variable selection.
# ---------------------------------------------------------------------------

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


def _word_pat(name: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9_])" + re.escape(name.lower())
                      + r"(?![a-z0-9_])")


def _summary_mentions(variables: list[dict], summary: str) -> list[dict]:
    """Variables named in the summary text -- the summarizer's judgment of
    which knobs matter, in the module author's own vocabulary."""
    if not summary:
        return []
    low = summary.lower()
    return [v for v in variables if _word_pat(v["name"]).search(low)]


def _strip_tables(readme: str) -> str:
    """Drop markdown table rows. Tables are reference listings -- a
    variable sitting alphabetically next to a keyword in the inputs
    table is not a semantic relationship. Prose and code examples are
    where authors show settings that belong together, so only those
    feed the co-mention signal."""
    return "\n".join(ln for ln in readme.splitlines()
                     if not ln.lstrip().startswith("|"))


def _readme_related(variables: list[dict], readme: str, use_case: str,
                    window: int = 1200) -> list[dict]:
    """Variables mentioned near use-case keywords in the README. Module
    READMEs show settings that belong together (e.g. a static-website
    example that also flips the S3 public-access blocks); a variable
    named in that neighbourhood is one the use case depends on."""
    if not readme or not use_case:
        return []
    keywords = {k for k in _tokens(use_case) if len(k) >= 4}
    if not keywords:
        return []
    low = _strip_tables(readme).lower()
    spans: list[tuple[int, int]] = []
    for kw in keywords:
        start = 0
        while len(spans) < 40:
            i = low.find(kw, start)
            if i < 0:
                break
            spans.append((max(0, i - window), min(len(low), i + window)))
            start = i + len(kw)
    if not spans:
        return []
    return [v for v in variables
            if any(_word_pat(v["name"]).search(low[a:b]) for a, b in spans)]


def _is_deprecated(var: dict) -> bool:
    """The module author marked this variable deprecated (e.g. AWS
    deprecated S3 ACLs in favor of bucket policies)."""
    return "deprecat" in (var.get("description") or "").lower()


def _deprecation_note(var: dict) -> str:
    for line in (var.get("description") or "").splitlines():
        if "deprecat" in line.lower():
            return line.strip().strip(".")[:110]
    return "deprecated by the module author"


def _conflicts_with(var: dict) -> str | None:
    """The other variable this one declares a conflict with, if any
    (e.g. grants: 'Conflicts with `acl`')."""
    m = re.search(r"conflicts with\s+`([^`]+)`",
                  var.get("description") or "", re.IGNORECASE)
    return m.group(1) if m else None


def select_variables(variables: list[dict], use_case: str = "",
                     summary: str = "", readme: str = "",
                     max_relevant: int = 10) -> tuple[list[dict], list[dict]]:
    """Split into (required, relevant-optional) variables for the snippet.

    Relevant = direct keyword matches first, then variables named in the
    summary, then variables mentioned near use-case keywords in the
    README. Capped so the snippet stays a snippet. Deprecated variables
    are only suggested when the use case names them directly -- a README
    co-mention is not enough to recommend a deprecated setting.
    """
    required = [v for v in variables if _is_required(v)]
    if not use_case:
        return required, []
    seen = {v["name"] for v in required}
    relevant: list[dict] = []

    def add(cands: list[dict]):
        for v in cands:
            if v["name"] not in seen and len(relevant) < max_relevant:
                seen.add(v["name"])
                relevant.append(v)

    use_case_tokens = _tokens(use_case)
    keyword_matched = {v["name"] for v in variables
                       if _relevance(v, use_case_tokens) > 0}

    def fresh(cands: list[dict]) -> list[dict]:
        return [v for v in cands
                if not _is_deprecated(v) or v["name"] in keyword_matched]

    add([v for v in variables if v["name"] in keyword_matched])
    add(fresh(_summary_mentions(variables, summary)))
    add(fresh(_readme_related(variables, readme, use_case)))
    return required, relevant


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

def extract_source(readme: str | None) -> str | None:
    """Best-effort registry source from the README's own usage example
    (e.g. source = "terraform-aws-modules/s3-bucket/aws")."""
    m = re.search(r'source\s*=\s*"([^"\n]+)"', readme or "")
    return m.group(1) if m else None


def _fmt_default(value) -> str:
    if isinstance(value, str):
        v = value if len(value) <= 40 else value[:37] + "..."
        return f'"{v}"'
    try:
        s = json.dumps(value)
    except (TypeError, ValueError):
        s = str(value)
    return s if len(s) <= 40 else s[:37] + "..."


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

    required, relevant = select_variables(variables, use_case,
                                          summary=summary, readme=readme)

    lines: list[str] = []
    if summary:
        first = summary.strip().split("\n")[0][:120]
        lines.append(f"# {first}")
    lines.append(f'module "{label}" {{')

    source = extract_source(readme)
    if source:
        lines.append(f'  source = "{source}"')
        if "/" in source and not source.startswith((".", "/")):
            lines.append('  # version = "x.y.z"  # pin a version')
    else:
        lines.append('  source = "<MODULE_SOURCE>"  # TODO: set the module source')

    if required or relevant:
        lines.append("")
    all_vars = required + relevant
    selected_names = {v["name"] for v in all_vars}
    name_w = max((len(v["name"]) for v in all_vars), default=0)

    def notes_for(v: dict) -> str:
        notes = []
        if _is_deprecated(v):
            notes.append(f"DEPRECATED: {_deprecation_note(v)}")
        other = _conflicts_with(v)
        if other and other in selected_names and other != v["name"]:
            notes.append(f"conflicts with {other} -- use one or the other")
        return (" -- " + "; ".join(notes)) if notes else ""

    for v in required:
        pad = " " * (name_w - len(v["name"]))
        val = _example_for_type(v.get("type"))
        lines.append(f'  {v["name"]}{pad} = {val}'
                     f'  # TODO: required, no default{notes_for(v)}')
    if required and relevant:
        lines.append("")
    if relevant:
        if use_case:
            lines.append(f'  # Relevant to "{use_case}":')
        for v in relevant:
            pad = " " * (name_w - len(v["name"]))
            if v.get("type") == "bool":
                # No honest placeholder for a bool: flipping one the wrong
                # way is worse than a no-op line, so show the default and
                # let the caller decide (e.g. some public-access blocks
                # stay true while others go false for a website).
                default = v.get("default")
                val = _fmt_default(default) if default is not None else "false"
                comment = f"# default: {_fmt_default(default)}{notes_for(v)}"
            else:
                val = _example_for_type(v.get("type"))
                comment = (f"# default: {_fmt_default(v.get('default'))}"
                           f" — change to enable{notes_for(v)}")
            lines.append(f'  {v["name"]}{pad} = {val}  {comment}')
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
