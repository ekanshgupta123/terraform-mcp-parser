"""Interactive first-time setup: index your modules, then connect a client.

Walks a new user through the one-time setup the README describes:
  1. Point at the folder holding their Terraform modules.
  2. Provide an Anthropic API key (one-time, for the summarizer).
  3. Run the indexing pipeline (reuses terraform_mcp_parser.pipeline).
  4. Verify the index is non-empty.
  5. Print the exact client config to paste into Claude Code / VS Code.

Usage:
    terraform-mcp-setup [--summaries-json FILE]

--summaries-json is the same escape hatch as terraform-mcp-index: load
pre-computed summaries instead of calling the Anthropic API (handy for
testing without spending API calls). When it is given, no API key is
needed.
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _prompt(text: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{text}{suffix}: ").strip()
    return answer or (default or "")


def _confirm(text: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = input(f"{text} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _find_module_dirs(modules_dir: Path) -> list[Path]:
    """Subdirectories that look like Terraform modules (contain .tf files)."""
    found = []
    for child in sorted(modules_dir.iterdir()):
        if child.is_dir() and any(child.glob("*.tf")):
            found.append(child)
    return found


def _step_modules_dir() -> Path:
    print("\nStep 1 of 4: your Terraform modules")
    print("Point me at a folder with one subfolder per module.")
    while True:
        raw = _prompt("Path to your modules folder")
        if not raw:
            print("  Please enter a path (or Ctrl-C to quit).")
            continue
        modules_dir = Path(raw).expanduser().resolve()
        if not modules_dir.is_dir():
            print(f"  Not a folder: {modules_dir}")
            continue
        module_dirs = _find_module_dirs(modules_dir)
        if not module_dirs:
            print(f"  No subfolders with .tf files found in {modules_dir}")
            print("  Each module should be its own subfolder "
                  "(variables.tf, outputs.tf, main.tf).")
            continue
        print(f"  Found {len(module_dirs)} module(s):")
        for d in module_dirs:
            print(f"    - {d.name}")
        if _confirm("Index these modules?"):
            return modules_dir
        print("  Okay -- enter a different path.")


def _step_api_key(summaries_json: str | None) -> str | None:
    """Return an API key, or None when --summaries-json skips the API."""
    print("\nStep 2 of 4: Anthropic API key")
    if summaries_json:
        print("  Skipped (--summaries-json given, no API calls needed).")
        return None
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        print("  Found ANTHROPIC_API_KEY in your environment -- using it.")
        return key
    print("  Each module is summarized once by the Anthropic API. This is")
    print("  the only paid step: a $5 credit covers hundreds of modules,")
    print("  and re-running only re-summarizes modules you add or change.")
    while True:
        try:
            pasted = getpass.getpass("  Paste your Anthropic API key: ").strip()
        except Exception:  # no tty (piped stdin) -- fall back to visible input
            pasted = input("  Paste your Anthropic API key: ").strip()
        if pasted:
            key = pasted
            break
        print("  No key entered -- it is required to summarize modules.")
    os.environ["ANTHROPIC_API_KEY"] = key
    if _confirm("  Save it to ./.env so you don't have to paste it again?",
               default=True):
        _save_env_key(key)
    else:
        print("  Not saved -- you'll need to paste it again next time.")
    return key


def _save_env_key(key: str) -> None:
    """Write ANTHROPIC_API_KEY into ./.env (created with mode 0600).

    The key value is never printed.
    """
    env_path = Path(".env")
    lines: list[str] = []
    if env_path.exists():
        lines = [ln for ln in env_path.read_text().splitlines()
                 if not ln.strip().startswith("ANTHROPIC_API_KEY=")]
    lines.append(f"ANTHROPIC_API_KEY={key}")
    # Restrict permissions before writing the secret (best effort on all OSes).
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        os.close(fd)
        raise
    print("  Saved to ./.env (file is readable only by you).")


def _step_index(modules_dir: Path, summaries_json: str | None) -> None:
    print("\nStep 3 of 4: indexing (this can take a few minutes)")
    print("  Re-running is safe -- modules already in the index are replaced,")
    print("  not duplicated.")
    # Reuse the existing pipeline entry point without reimplementing it.
    # pipeline.main() parses sys.argv, so hand it the argv it expects.
    from . import pipeline

    argv = [sys.argv[0], str(modules_dir)]
    if summaries_json:
        argv += ["--summaries-json", summaries_json]
    old_argv = sys.argv
    sys.argv = argv
    try:
        pipeline.main()
    finally:
        sys.argv = old_argv


def _index_stats() -> tuple[int, int, str]:
    """(record count, distinct modules, absolute chroma path)."""
    from .store import COLLECTION_NAME, get_collection

    collection = get_collection()
    records = collection.count()
    modules: set[str] = set()
    if records:
        res = collection.get(include=["metadatas"], limit=records)
        for meta in res.get("metadatas") or []:
            name = (meta or {}).get("module_name")
            if name:
                modules.add(name)
    from .store import CHROMA_PATH
    return records, len(modules), os.path.abspath(CHROMA_PATH)


def _step_verify() -> str:
    print("\nStep 4 of 4: verifying the index")
    records, n_modules, chroma_path = _index_stats()
    if records == 0 or n_modules == 0:
        print("  The index is empty -- nothing was stored.")
        print("  Check that your modules folder really contains .tf files,")
        print("  then re-run: terraform-mcp-setup")
        sys.exit(1)
    print(f"  Index looks good: {n_modules} module(s), "
          f"{records} records at {chroma_path}")
    return chroma_path


def _server_command() -> str:
    """Absolute path to the installed server script, with a fallback."""
    script = Path(sys.executable).parent / "terraform-mcp-parser"
    if script.exists():
        return str(script)
    return f"{sys.executable} -m terraform_mcp_parser"


def _print_client_config(chroma_path: str) -> None:
    server_cmd = _server_command()
    print("\nAll set. Connect a client (one-time each):\n")
    print("Claude Code (terminal):")
    print()
    print("  claude mcp add terraform-mcp-parser \\")
    print(f"    -e CHROMA_PATH={chroma_path} \\")
    print(f"    -- {server_cmd}")
    print()
    print("VS Code (.vscode/mcp.json in your workspace):")
    print()
    print("  {")
    print('    "servers": {')
    print('      "terraform-parser": {')
    print('        "type": "stdio",')
    print(f'        "command": "{server_cmd}",')
    print('        "env": {')
    print(f'          "CHROMA_PATH": "{chroma_path}"')
    print("        }")
    print("      }")
    print("    }")
    print("  }")
    print()
    print("Then just ask in plain language, e.g. "
          '"is there a module for a private encrypted database?"')


def main() -> None:
    argp = argparse.ArgumentParser(
        description="Guided first-time setup for terraform-mcp-parser.")
    argp.add_argument("--summaries-json", default=None,
                      help="Pre-computed summaries keyed by module name; "
                           "skips the Anthropic API (and the API key step).")
    args = argp.parse_args()

    print("terraform-mcp-setup: let's get your modules searchable.")
    try:
        modules_dir = _step_modules_dir()
        _step_api_key(args.summaries_json)
        _step_index(modules_dir, args.summaries_json)
        chroma_path = _step_verify()
    except KeyboardInterrupt:
        print("\nSetup cancelled -- you can re-run terraform-mcp-setup "
              "any time.")
        sys.exit(130)
    _print_client_config(chroma_path)


if __name__ == "__main__":
    main()
