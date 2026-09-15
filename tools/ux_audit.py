"""UI/UX audit by Gemini: screenshot the running app, send the screens plus
the product brief to Gemini's best model, get ranked findings as Markdown.

    # 1. screenshots of a project (make it from tests/fixtures/sample.step --
    #    customer CAD is NDA and must never leave this machine)
    python tools/ux_audit.py capture --base http://localhost:5173 --project 1 --shots /tmp/shots
    # 2. the audit
    python tools/ux_audit.py audit --shots /tmp/shots            # -> docs/ux-audit-<date>.md
    python tools/ux_audit.py models                              # what the key can use

Stdlib only. Key: GEMINI_API_KEY in the environment, else `.env` at repo root.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://generativelanguage.googleapis.com/v1beta"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_MODEL = "gemini-pro-latest"        # alias: whatever is the newest pro

# (file stem, hash route, window size). Two packaging captures: what the
# user sees first, and the whole scroll -- the "too much on one screen"
# complaint (PLANNING F1) is about the second one.
SCREENS = [
    ("01-projects", "#/projects", (1440, 900)),
    ("02-new-project", "#/projects/new", (1440, 900)),
    ("03-overview", "#/projects/{id}", (1440, 900)),
    ("04-packaging-first-viewport", "#/projects/{id}/packaging", (1440, 900)),
    ("05-packaging-full-scroll", "#/projects/{id}/packaging", (1440, 3200)),
    ("06-truck", "#/projects/{id}/truck", (1440, 900)),
    ("07-runs", "#/projects/{id}/runs", (1440, 900)),
    ("08-proposal", "#/projects/{id}/proposal", (1440, 900)),
    ("09-assets", "#/assets", (1440, 900)),
    ("10-load-calculator", "#/tools/load-calculator", (1440, 900)),
]

BRIEF_FILES = ["docs/USER_JOURNEY.md", "PLANNING.md"]

INSTRUCTIONS = """You are a senior product designer auditing an internal engineering
tool. You will get the product brief, then one screenshot per screen (each
preceded by its name and route). The users are packaging engineers who open
this tool to answer one question fast: how many parts fit in which box, and
what does the insert look like. They skim. They do not read.

Audit the UI and UX of what you SEE. Be specific to the pixels in front of
you: name the screen, the element, and what is wrong. Do not restate the brief
or invent screens you were not shown. Where the brief says users complained
about something, judge for yourself whether the screenshot shows it fixed,
half-fixed, or still present.

Output Markdown, in this order:
1. `## Verdict` -- three sentences: what this UI does well, its biggest
   problem, and what one change would matter most.
2. `## Top 10` -- ranked list, one line each: `P0/P1/P2 - screen - problem -> fix`.
3. `## Findings` -- one `### [P0|P1|P2] <title> -- <screen>` per finding, each
   with **Problem** (what the user experiences), **Evidence** (what in the
   screenshot shows it), **Fix** (concrete: layout, copy, control -- what
   it should look like instead), **Cost** (S/M/L). P0 = the user cannot
   complete or misreads the core task; P1 = slows or confuses; P2 = polish.
4. `## Keep` -- what is already right and must not be "improved" away.
5. `## Pushback` -- anything in the brief's own tickets you disagree with,
   and why.

Style rules for the tool itself, given as constraints not suggestions: light
ground (dark backgrounds failed with these users), Fira Sans / Fira Code,
blue #1E40AF primary, amber #D97706 accent, data-dense engineering
instrument, not a marketing page. Every number and drawing is computed by
the backend and must stay; you may hide, group, reorder, rename, or resize,
never remove information.
"""


def api_key() -> str:
    k = os.environ.get("GEMINI_API_KEY")
    if not k and (ROOT / ".env").exists():
        m = re.search(r"^GEMINI_API_KEY=(.+)$", (ROOT / ".env").read_text(), re.M)
        k = m.group(1).strip().strip('"') if m else None
    if not k:
        sys.exit("GEMINI_API_KEY not set and not in .env")
    return k


def gemini(path: str, body: dict | None = None, timeout: int = 900) -> dict:
    url = f"{API}/{path}{'&' if '?' in path else '?'}key={api_key()}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def capture(base: str, project: int, shots: Path, only: str | None) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="ux_audit_chrome_")   # never share a profile
    for stem, route, (w, h) in SCREENS:
        if only and only not in stem:
            continue
        url = base.rstrip("/") + "/" + route.format(id=project)
        out = shots / f"{stem}.png"
        # No --disable-gpu: the 3D viewers need WebGL, and without a GPU they
        # fail as an opaque "Script error".
        cmd = [CHROME, "--headless=new", "--hide-scrollbars",
               f"--user-data-dir={profile}", f"--window-size={w},{h}",
               "--virtual-time-budget=10000", f"--screenshot={out}", url]
        _chrome_shot(cmd, out)
        print(f"{out.name:36} {w}x{h}  {url}")


def _chrome_shot(cmd: list[str], out: Path, wait_s: float = 90.0) -> None:
    """Run Chrome until the PNG lands, then kill it.

    On macOS, `--headless=new --screenshot` without --disable-gpu writes the
    file in a few seconds and then never exits (loops on
    CVDisplayLinkCreateWithCGDisplay failed, CVReturn -6670). Waiting for the
    process therefore hangs every run; wait for the file instead.
    """
    out.unlink(missing_ok=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + wait_s
        size = -1
        while time.monotonic() < deadline:
            if out.exists() and out.stat().st_size == size and size > 0:
                return                       # written and stable across 0.5s
            size = out.stat().st_size if out.exists() else -1
            if proc.poll() is not None and out.exists():
                return
            time.sleep(0.5)
        if not out.exists():
            sys.exit(f"Chrome produced no screenshot for {out.name} in {wait_s:.0f}s")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def brief_text() -> str:
    parts = []
    for rel in BRIEF_FILES:
        p = ROOT / rel
        if p.exists():
            parts.append(f"\n\n===== {rel} =====\n{p.read_text()}")
    return "".join(parts)


def audit(shots: Path, model: str, out: Path | None) -> Path:
    pngs = sorted(shots.glob("*.png"))
    if not pngs:
        sys.exit(f"no PNGs in {shots}")
    parts = [{"text": INSTRUCTIONS},
             {"text": "===== PRODUCT BRIEF (our own documents; challenge them) ====="
                      + brief_text()},
             {"text": f"===== SCREENSHOTS ({len(pngs)}) ====="}]
    routes = {stem: route for stem, route, _ in SCREENS}
    for p in pngs:
        parts.append({"text": f"Screen `{p.stem}` -- route `{routes.get(p.stem, '?')}`:"})
        parts.append({"inline_data": {"mime_type": "image/png",
                                      "data": base64.b64encode(p.read_bytes()).decode()}})
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 24000}}
    resp = gemini(f"models/{model}:generateContent", body)
    try:
        text = "".join(pt.get("text", "") for pt in
                       resp["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        sys.exit("unexpected response:\n" + json.dumps(resp, indent=1)[:3000])
    usage = resp.get("usageMetadata", {})
    out = out or ROOT / "docs" / f"ux-audit-{dt.date.today().isoformat()}.md"
    head = (f"# UI/UX audit by {resp.get('modelVersion', model)} -- "
            f"{dt.date.today().isoformat()}\n\n"
            f"Screens: {', '.join(p.stem for p in pngs)}. Brief: {', '.join(BRIEF_FILES)}. "
            f"Tokens in/out: {usage.get('promptTokenCount', '?')}/"
            f"{usage.get('candidatesTokenCount', '?')}. "
            f"Generated by `tools/ux_audit.py`; PM triage goes in PLANNING.md, not here.\n\n")
    out.write_text(head + text)
    print(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--base", default="http://localhost:5173")
    c.add_argument("--project", type=int, required=True,
                   help="a project made from tests/fixtures/sample.step, NOT customer CAD")
    c.add_argument("--shots", type=Path, required=True)
    c.add_argument("--only", help="substring of a screen stem")
    a = sub.add_parser("audit")
    a.add_argument("--shots", type=Path, required=True)
    a.add_argument("--model", default=DEFAULT_MODEL)
    a.add_argument("--out", type=Path)
    sub.add_parser("models")
    args = ap.parse_args()
    if args.cmd == "capture":
        capture(args.base, args.project, args.shots, args.only)
    elif args.cmd == "audit":
        audit(args.shots, args.model, args.out)
    else:
        for m in gemini("models?pageSize=200").get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                print(m["name"].removeprefix("models/"))


if __name__ == "__main__":
    main()
