#!/usr/bin/env python3
"""
Sanitize a raw Workato project export so it can be published publicly.

Usage:
    python3 scripts/sanitize.py <raw_export_dir_or_zip> [out_dir]

What it does
  1. Copies every *.json file from the raw export into out_dir (default: ./workato).
  2. Replaces personal / account-specific values with placeholders:
       - every email address                -> <placeholder>@example.com
       - Google Sheets / Drive / Calendar IDs -> YOUR_..._ID
  3. Extracts the Claude system prompt, tool schemas and the Ruby parser into
     readable files (prompts/, src/) so reviewers don't have to dig through JSON.
  4. Audits the output and exits non-zero if anything that looks sensitive remains.

Credentials (API keys, OAuth tokens) are never included in Workato exports,
but the audit still checks for them in case a key was pasted into a recipe step.
"""
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Keys whose values are account-specific resource IDs.
ID_KEYS = {
    "spreadsheet_id": "YOUR_GOOGLE_SHEET_ID",
    "folder_id": "YOUR_DRIVE_FOLDER_ID",
    "calendar_id": "YOUR_CALENDAR_ID",
    "document_id": "YOUR_DOCUMENT_ID",
}

# Emails in known positions get a meaningful placeholder; any other email
# falls back to a generic one.
EMAIL_BY_CONTEXT = {
    "escalate_to_human": "owner@example.com",
    "receptionist_gmail_trigger": "receptionist-inbox@example.com",
}

# Patterns that must never appear in the published output.
AUDIT_PATTERNS = {
    "email address": EMAIL_RE,
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    "Bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{16,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    "Google resource ID": re.compile(r'"(1[A-Za-z0-9_-]{40,})"'),
}
ALLOWED_EMAIL_DOMAIN = "example.com"


def scrub(obj, file_stem):
    """Recursively replace sensitive values."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ID_KEYS and isinstance(v, str) and v:
                out[k] = ID_KEYS[k]
            else:
                out[k] = scrub(v, file_stem)
        return out
    if isinstance(obj, list):
        return [scrub(x, file_stem) for x in obj]
    if isinstance(obj, str):
        placeholder = EMAIL_BY_CONTEXT.get(file_stem.split(".")[0], "someone@example.com")
        return EMAIL_RE.sub(
            lambda m: m.group(0) if m.group(0).endswith("@" + ALLOWED_EMAIL_DOMAIN) else placeholder,
            obj,
        )
    return obj


def extract_readables(out_dir: Path):
    """Pull the prompt, tool schemas and Ruby code out of the main recipe."""
    trigger = out_dir / "receptionist_gmail_trigger.recipe.json"
    if not trigger.exists():
        return
    recipe = json.loads(trigger.read_text())

    def walk(node):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    for step in walk(recipe):
        if step.get("provider") == "rest" and step.get("keyword") == "action":
            body = step["input"]["request"]["body"]
            system = re.search(r'"system":"(.*?)","tools":', body, re.S)
            tools = re.search(r'"tools":(\[.*\]),"messages"', body, re.S)
            model = re.search(r'"model":"(.*?)"', body)
            if system:
                (ROOT / "prompts").mkdir(exist_ok=True)
                (ROOT / "prompts" / "system-prompt.md").write_text(
                    "# System prompt\n\n"
                    f"Model: `{model.group(1) if model else 'unknown'}`\n\n"
                    "Sent as the `system` field of the Claude Messages API call in "
                    "`receptionist_gmail_trigger`.\n\n"
                    "```text\n" + re.sub(r"(?<=\.)\s+(?=[A-Z])", "\n", system.group(1)) + "\n```\n"
                )
            if tools:
                (ROOT / "prompts" / "tools.json").write_text(
                    json.dumps(json.loads(tools.group(1)), indent=2) + "\n"
                )
        if step.get("provider") == "workato_custom_code" and step.get("keyword") == "action":
            (ROOT / "src").mkdir(exist_ok=True)
            (ROOT / "src" / "parse_tool_calls.rb").write_text(
                "# Workato custom Ruby step: parses Claude's tool_use blocks into flat\n"
                "# fields that the callable recipes consume. Extracted from\n"
                "# receptionist_gmail_trigger.recipe.json (step 4).\n\n"
                + step["input"]["code"].rstrip() + "\n"
            )


def audit(paths):
    problems = []
    for p in paths:
        text = p.read_text(errors="ignore")
        for label, rx in AUDIT_PATTERNS.items():
            for m in rx.finditer(text):
                hit = m.group(0)
                if label == "email address" and hit.endswith("@" + ALLOWED_EMAIL_DOMAIN):
                    continue
                problems.append(f"{p.relative_to(ROOT)}: possible {label}: {hit[:12]}…")
    return problems


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1]).expanduser()
    out_dir = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else ROOT / "workato"

    tmp = None
    if src.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp())
        zipfile.ZipFile(src).extractall(tmp)
        src = tmp

    out_dir.mkdir(parents=True, exist_ok=True)  # files are overwritten in place

    for f in sorted(src.rglob("*.json")):
        data = json.loads(f.read_text())
        clean = scrub(data, f.name)
        (out_dir / f.name).write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n")
        print(f"sanitized  {f.name}")

    if tmp:
        shutil.rmtree(tmp)

    extract_readables(out_dir)

    published = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts
                 and p.suffix in {".json", ".md", ".rb", ".txt"}]
    problems = audit(published)
    if problems:
        print("\nAUDIT FAILED — review before publishing:")
        print("\n".join("  " + x for x in problems))
        sys.exit(1)
    print("\nAudit passed: no emails, keys or resource IDs found.")


if __name__ == "__main__":
    main()
