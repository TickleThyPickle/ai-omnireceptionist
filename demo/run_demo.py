#!/usr/bin/env python3
"""
Run the receptionist's Claude step locally, without Workato.

It sends a sample customer email to Claude using the exact system prompt and
tool definitions from the Workato recipe. It then prints which tools Claude
chose and what Workato would do with each one. Nothing is actually emailed,
booked or logged.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 demo/run_demo.py                          # run every sample email
    python3 demo/run_demo.py demo/sample_emails/booking.txt
    python3 demo/run_demo.py --dry-run                # show the request, no API call
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT / "prompts" / "system-prompt.md"
TOOLS_FILE = ROOT / "prompts" / "tools.json"
SAMPLES_DIR = ROOT / "demo" / "sample_emails"


def load_prompt():
    """Read the model and system prompt exactly as the Workato recipe sends them."""
    text = PROMPT_FILE.read_text()
    model = re.search(r"Model: `(.+?)`", text).group(1)
    block = re.search(r"```text\n(.*?)\n```", text, re.S).group(1)
    system = " ".join(line.strip() for line in block.splitlines() if line.strip())
    return model, system


def load_email(path: Path):
    """Sample format: 'From:' and 'Subject:' header lines, a blank line, then the body."""
    headers, _, body = path.read_text().partition("\n\n")
    fields = dict(line.split(":", 1) for line in headers.splitlines() if ":" in line)
    return fields["From"].strip(), fields["Subject"].strip(), body.strip()


def workato_clean_body(sender, subject, body):
    """Same flattening as the recipe's `clean_body` variable step."""
    flat = body.replace("\n", " ").replace("\r", " ").replace('"', "'").replace("\\", " ")
    return f"From: {sender}, Subject: {subject}, Message: {flat}"


def describe(tool, args, sender):
    """Explain what the Workato callable recipe would do with this tool call."""
    if tool == "send_reply":
        return (f"Gmail → send reply to {args.get('recipient')}\n"
                f"    Subject: {args.get('subject')}\n"
                + "\n".join("    | " + line for line in args.get("message", "").splitlines()))
    if tool == "book_appointment":
        raw = args.get("datetime", "")
        try:
            start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            end = start + timedelta(hours=1)  # the Ruby step books a one-hour slot
            when = f"{start:%a %d %b %Y, %H:%M} – {end:%H:%M}"
        except ValueError:
            when = f"{raw} (not valid ISO 8601 — the Workato Ruby step would fail here)"
        return (f"Google Calendar → event \"{args.get('name')} - {args.get('service')}\"\n"
                f"    {when}"
                + (f"\n    Notes: {args['notes']}" if args.get("notes") else ""))
    if tool == "log_conversation":
        return (f"Google Sheets → new row: "
                f"[{args.get('sender')}] [{args.get('intent')}] [{args.get('action_taken')}]")
    if tool == "escalate_to_human":
        return f"Gmail → alert the owner. Claude's reason: {args.get('reason')}"
    return f"(unknown tool) {json.dumps(args)}"


def run(path: Path, client, model, system, tools, dry_run):
    sender, subject, body = load_email(path)
    user_message = workato_clean_body(sender, subject, body)

    print("=" * 72)
    print(f"{path.name}\n  From: {sender}\n  Subject: {subject}")
    print("-" * 72)

    if dry_run:
        print(json.dumps({"model": model, "max_tokens": 1000, "system": system,
                          "tools": [t["name"] for t in tools],
                          "messages": [{"role": "user", "content": user_message}]},
                         indent=2, ensure_ascii=False))
        return

    response = client.messages.create(
        model=model,
        max_tokens=1000,  # same limit as the Workato recipe
        system=system,
        tools=tools,
        messages=[{"role": "user", "content": user_message}],
    )

    calls = [b for b in response.content if b.type == "tool_use"]
    if response.stop_reason != "tool_use" or not calls:
        print(f"Claude did not call any tools (stop_reason={response.stop_reason}).")
        print("In Workato, the recipe would stop here without acting.")
        return

    for i, call in enumerate(calls, 1):
        print(f"{i}. {call.name}")
        print("    " + describe(call.name, call.input, sender))
    usage = response.usage
    print(f"\n  tokens: {usage.input_tokens} in / {usage.output_tokens} out")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("emails", nargs="*", type=Path, help="sample email files (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="print the request without calling Claude")
    args = parser.parse_args()

    model, system = load_prompt()
    tools = json.loads(TOOLS_FILE.read_text())
    emails = args.emails or sorted(SAMPLES_DIR.glob("*.txt"))

    client = None
    if not args.dry_run:
        try:
            import anthropic
        except ImportError:
            sys.exit("Install the SDK first:  pip install -r demo/requirements.txt")
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

    for path in emails:
        run(path, client, model, system, tools, args.dry_run)
    print("=" * 72)


if __name__ == "__main__":
    main()
