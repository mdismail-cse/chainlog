#!/usr/bin/env python3
"""Regenerate the README screenshots from REAL chainlog output.

Builds a throwaway demo HOME (example repo "shop", PR 4321 tested over three rounds in Claude and
Codex), runs the actual commands with colour forced on, and renders each output as a terminal-style
SVG in docs/images/. Nothing outside a temp directory is read or written.

    python3 docs/screenshots.py
"""
import builtins
import calendar
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "src"))
OUT = ROOT / "docs" / "images"
COLS = 96

from chainlog import cli  # noqa: E402
from chainlog.runtime import Runtime  # noqa: E402

NOW = time.time()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


# --- demo data ---------------------------------------------------------------------------------
class Demo:
    def __init__(self, tmp):
        self.home = Path(tmp) / "home"
        self.data = Path(tmp) / "data"
        self.shop = self.home / "code" / "shop"
        self.tracker = self.home / "code" / "tracker"
        for repo, remote in ((self.shop, "acme/shop"), (self.tracker, "acme/tracker")):
            (repo / ".git").mkdir(parents=True)
            (repo / ".git" / "config").write_text('[remote "origin"]\n\turl = git@github.com:%s.git\n' % remote)
        (self.home / ".claude" / "projects").mkdir(parents=True)
        (self.home / ".codex" / "sessions").mkdir(parents=True)

    def claude(self, sid, cwd, start, end, title, prompt, work, pr=None, branch="main"):
        d = self.home / ".claude" / "projects" / str(cwd).replace("/", "-")
        d.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "user", "sessionId": sid, "cwd": str(cwd), "gitBranch": branch, "version": "2.1.303",
             "entrypoint": "cli", "timestamp": iso(start), "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "sessionId": sid, "cwd": str(cwd), "timestamp": iso(end),
             "message": {"role": "assistant", "content": [{"type": "text", "text": work}]}},
            {"type": "last-prompt", "lastPrompt": prompt, "sessionId": sid},
            {"type": "ai-title", "aiTitle": title, "sessionId": sid},
        ]
        if pr:
            lines.append({"type": "pr-link", "prNumber": pr, "prRepository": "acme/shop",
                          "prUrl": "https://github.com/acme/shop/pull/%d" % pr, "sessionId": sid})
        p = d / (sid + ".jsonl")
        p.write_text("".join(json.dumps(x) + "\n" for x in lines))
        os.utime(p, (end, end))

    def codex(self, sid, cwd, start, end, name, prompt, work):
        d = self.home / ".codex" / "sessions" / "2026" / "10" / "19"
        d.mkdir(parents=True, exist_ok=True)
        meta = {"id": sid, "timestamp": iso(start), "cwd": str(cwd), "cli_version": "0.161.0", "source": "cli",
                "originator": "codex-tui", "git": {"branch": "fix/4321", "repository_url": "git@github.com:acme/shop.git"}}
        lines = [{"timestamp": iso(start), "type": "session_meta", "payload": meta},
                 {"timestamp": iso(start), "type": "response_item", "payload": {
                     "type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}},
                 {"timestamp": iso(end), "type": "event_msg", "payload": {"type": "agent_message", "message": work}}]
        p = d / ("rollout-2026-10-19T00-00-00-%s.jsonl" % sid)
        p.write_text("".join(json.dumps(x) + "\n" for x in lines))
        os.utime(p, (end, end))
        with open(self.home / ".codex" / "session_index.jsonl", "a") as f:
            f.write(json.dumps({"id": sid, "thread_name": name, "updated_at": iso(end)}) + "\n")

    def hook_end(self, sid, t, anc, fp=None):
        d = self.data / "spool"
        d.mkdir(parents=True, exist_ok=True)
        b = "%d-%d" % (t, abs(hash(sid)) % 99999)
        (d / (b + ".in")).write_text(json.dumps({"session_id": sid, "reason": "prompt_input_exit"}))
        f = {"herdr_pane": "", "herdr_ws": "", "herdr_tab": "", "tmux": "", "term": "", "iterm": ""}
        f.update(fp or {})
        (d / (b + ".json")).write_text(json.dumps({"v": 1, "h": "claude", "e": "end", "t": int(t), "anc": anc,
                                                   "tty": "", "fp": f, "chain": ""}))

    def live(self, sid, cwd):
        """A registry entry for a running session (this process), so lists show ▶."""
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
        local = calendar.timegm(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
        utc = local - (time.localtime().tm_gmtoff or 0)
        (self.home / ".claude" / "sessions").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / "sessions" / ("%d.json" % os.getpid())).write_text(json.dumps({
            "pid": os.getpid(), "sessionId": sid, "cwd": str(cwd), "kind": "interactive", "entrypoint": "cli",
            "procStart": time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(utc)), "status": "busy"}))


def build(demo):
    h = 3600
    d = 86400
    demo.claude("3f9a1c2e-1111-4a00-8000-000000000001", demo.shop, NOW - 3 * d, NOW - 3 * d + 2 * h,
                "Shop PR 4321 checkout flow", "test checkout with a saved card on staging",
                "Checkout works with saved cards; receipt email arrives.", pr=4321, branch="fix/4321")
    demo.claude("a01c77d4-2222-4a00-8000-000000000002", demo.shop, NOW - 2 * d, NOW - 2 * d + h,
                "Shop PR 4321 partial refund", "now test a partial refund with a coupon applied",
                "Partial refund with a 10% coupon returns HTTP 500 from Order::refund().", pr=4321, branch="fix/4321")
    demo.codex("01a2b3c4-7918-7aa0-b147-4a09bb6f549c", demo.shop, NOW - 5 * h, NOW - 4 * h,
               "PR 4321 retest after the fix", "retest partial refunds after the fix",
               "Partial refunds work now. Edge case: refund > coupon value still rounds wrong.")
    demo.claude("7d4be3ab-3333-4a00-8000-000000000003", demo.tracker, NOW - 2 * h - 1800, NOW - 2 * h,
                "Tracker sprint 22 item 342", "verify the sprint board filters",
                "Filters work; the 'assigned to me' toggle resets after reload.")
    demo.claude("c0ffee12-4444-4a00-8000-000000000004", demo.shop, NOW - 26 * h, NOW - 25 * h,
                "Fix flaky login test", "the login e2e test fails 1 in 5 runs, find out why",
                "The test races the session cookie; added a wait for the redirect.")
    demo.claude("b5e6f7a8-5555-4a00-8000-000000000005", demo.tracker, NOW - 20 * 60, NOW - 30,
                "Tracker API rate limits", "load-test the public API", "Running k6 with 50 virtual users…")
    demo.live("b5e6f7a8-5555-4a00-8000-000000000005", demo.tracker)
    # the session the hero image saves: it just ended in this shell (pid 4242)
    demo.claude("9b8c7d6e-6666-4a00-8000-000000000006", demo.shop, NOW - 50 * 60, NOW - 45,
                "Shop PR 4321 coupon rounding", "check refund rounding when the refund exceeds the coupon",
                "Rounding fixed for refunds above the coupon value; all 14 refund cases pass.", pr=4321,
                branch="fix/4321")
    demo.hook_end("9b8c7d6e-6666-4a00-8000-000000000006", NOW - 40, anc=[5001, 4242])


# --- running chainlog ----------------------------------------------------------------------------
def run(demo, argv, tty=False, answers=(), cwd=None, ppid=4242):
    env = {"HOME": str(demo.home), "CHAINLOG_HOME": str(demo.data), "PATH": "/usr/bin:/bin",
           "FORCE_COLOR": "1", "PWD": str(cwd or demo.shop), "TERM": "xterm-256color"}
    rt = Runtime(env=env, home=demo.home, ppid=ppid, proctable={4242: (1, "-zsh")}, tty=tty, tty_name="")
    answers = list(answers)
    real_input = builtins.input
    buf = io.StringIO()

    def fake_input(prompt=""):
        ans = answers.pop(0) if answers else ""
        buf.write(prompt + "\x1b[1m" + ans + "\x1b[0m\n")  # show what the user typed
        return ans

    builtins.input = fake_input
    try:
        with redirect_stdout(buf):
            cli.main(argv, rt=rt)
    finally:
        builtins.input = real_input
    out = buf.getvalue()
    # the demo lives in a temp dir: show the paths a real install would have
    swaps = [(str(ROOT / "plugin"), "~/.local/share/chainlog/app/current/plugin"),
             (str(demo.data), "~/.local/share/chainlog"), (str(demo.home), "~")]
    for a, b in swaps:
        for v in sorted({a, os.path.realpath(a)}, key=len, reverse=True):
            out = out.replace(v, b)
    return out


# --- ANSI -> SVG ---------------------------------------------------------------------------------
COLORS = {"31": "#ff7b72", "32": "#7ee787", "33": "#e3b341", "36": "#79c0ff"}
FG, BG, DIM, PROMPT = "#e6edf3", "#0d1117", "#8b949e", "#7ee787"
SGR = re.compile(r"\x1b\[([0-9;]*)m")


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def segments(line):
    style = {"bold": False, "dim": False, "color": None}
    pos, out = 0, []
    for m in SGR.finditer(line):
        if m.start() > pos:
            out.append((line[pos:m.start()], dict(style)))
        for code in (m.group(1) or "0").split(";"):
            if code in ("0", ""):
                style = {"bold": False, "dim": False, "color": None}
            elif code == "1":
                style["bold"] = True
            elif code == "2":
                style["dim"] = True
            elif code in COLORS:
                style["color"] = COLORS[code]
        pos = m.end()
    if pos < len(line):
        out.append((line[pos:], dict(style)))
    return out


PREVIEW = None  # set by --preview DIR: also write square, fully scaled copies (for QuickLook checks)


def svg(text, title, path):
    lines = text.rstrip("\n").split("\n")
    cw, lh, pad, top = 8.43, 21, 18, 44
    width = int(max(len(SGR.sub("", ln)) for ln in lines) * cw + 2 * pad + 4)
    width = max(width, 520)
    height = top + len(lines) * lh + pad
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
             'font-family="SFMono-Regular, Menlo, Monaco, Consolas, \'Liberation Mono\', monospace" font-size="14">'
             % (width, height, width, height),
             '<rect width="100%%" height="100%%" rx="10" fill="%s"/>' % BG,
             '<rect width="100%%" height="32" rx="10" fill="#161b22"/><rect y="22" width="100%%" height="10" fill="#161b22"/>',
             '<circle cx="20" cy="16" r="6" fill="#ff5f56"/><circle cx="40" cy="16" r="6" fill="#ffbd2e"/>'
             '<circle cx="60" cy="16" r="6" fill="#27c93f"/>',
             '<text x="%d" y="21" fill="%s" font-size="12" text-anchor="middle">%s</text>' % (width // 2, DIM, esc(title))]
    for i, line in enumerate(lines):
        y = top + (i + 1) * lh - 6
        spans = []
        prompt = line.startswith("$ ")
        for txt, st in segments(line):
            attrs = []
            color = st["color"] or (DIM if st["dim"] else FG)
            if prompt and not st["color"] and not st["dim"]:
                color = FG
            attrs.append('fill="%s"' % color)
            if st["bold"]:
                attrs.append('font-weight="700"')
            spans.append("<tspan %s>%s</tspan>" % (" ".join(attrs), esc(txt)))
        if prompt:
            spans[0] = spans[0].replace(">$ ", '><tspan fill="%s">$</tspan> ' % PROMPT, 1)
        parts.append('<text x="%d" y="%d" xml:space="preserve">%s</text>' % (pad, y, "".join(spans)))
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n")
    if PREVIEW:
        side = max(width, height)
        head = parts[0].replace('width="%d" height="%d"' % (width, height), 'width="%d" height="%d"' % (side, side), 1)
        head = head.replace("<svg ", '<svg preserveAspectRatio="xMinYMin meet" ', 1)
        (Path(PREVIEW) / path.name).write_text("\n".join([head] + parts[1:]) + "\n")


def prompt(cmd):
    return "$ " + cmd + "\n"


# --- the Mac menu bar app, drawn from the JSON it reads (`chainlog _menu`) -----------------------------
def menubar_svg(menu_json, path):
    """An illustration of the menu bar app: same lists, wording and order as the real menu."""
    d = json.loads(menu_json)
    W, RH, X = 470, 23, 18
    TEXT, SUB, LINE, BODY = "#f2f2f7", "#9a9aa2", "#48484c", "#2a2a2d"
    out, y = [], 38  # y of the next row's top; the body starts below the menu bar strip

    def header(txt):
        nonlocal y
        out.append('<text x="%d" y="%d" fill="%s" font-size="11" font-weight="600">%s</text>' % (X, y + 15, SUB, esc(txt)))
        y += 22

    def sep():
        nonlocal y
        y += 5
        out.append('<line x1="0" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (y, W, y, LINE))
        y += 6

    def text(label, right="", chevron=False, dim=False, bold=False, icon=None, indent=0):
        nonlocal y
        cy = y + RH / 2
        if icon == "none":
            out.append('<circle cx="%d" cy="%.1f" r="4.5" fill="none" stroke="%s" stroke-width="1.5"/>' % (X + 6, cy, TEXT))
        elif icon == "saved":
            out.append('<circle cx="%d" cy="%.1f" r="5" fill="%s"/>' % (X + 6, cy, TEXT))
        elif icon == "auto":
            out.append('<circle cx="%d" cy="%.1f" r="4.5" fill="none" stroke="%s" stroke-width="1.5"/>'
                       '<path d="M%d %.1f a4.5 4.5 0 0 0 0 9z" fill="%s"/>' % (X + 6, cy, TEXT, X + 6, cy - 4.5, TEXT))
        elif icon == "live":
            out.append('<path d="M%d %.1f l9 5.5 l-9 5.5z" fill="%s"/>' % (X + 2, cy - 5.5, TEXT))
        out.append('<text x="%d" y="%.1f" fill="%s" font-size="13.5"%s>%s</text>' % (
            X + 28 - (0 if icon else 28) + indent, cy + 4.7, SUB if dim else TEXT, ' font-weight="600"' if bold else "", esc(label)))
        if right:
            out.append('<text x="%d" y="%.1f" fill="%s" font-size="11.5" text-anchor="end">%s</text>' % (
                W - 16 - (12 if chevron else 0), cy + 4.2, SUB, esc(right)))
        if chevron:
            out.append('<path d="M%d %.1f l4 4 l-4 4" fill="none" stroke="%s" stroke-width="1.6"/>' % (W - 20, cy - 4, SUB))
        y += RH

    def row(r):
        right = "  ".join(x for x in ((r["chain"] + (" r%s" % r["round"] if r.get("round") else "")) if r["chain"] else None,
                                       r["status"], r["ago"]) if x)
        icon = "live" if r["live"] else {"user": "saved", "auto": "auto"}.get(r["saved"], "none")
        text(r["title"][:44], right, icon=icon)

    y += 2
    header("Unsaved · %d" % d["counts"]["unsaved"])
    for r in d["unsaved"]:
        row(r)
    if d["counts"]["unsaved"] > len(d["unsaved"]):
        text("Review all %d unsaved…" % d["counts"]["unsaved"])
    sep()
    header("Recent")
    for r in d["recent"]:
        row(r)
    if d["chains"]:
        sep()
        header("PR chains")
        for c in d["chains"]:
            text(c["id"], "  ".join(x for x in (c["status"], "%d rounds" % c["rounds"], c["ago"]) if x), chevron=True)
    sep()
    text("Click a session to reopen it · ⌥-click copies its command", dim=True)
    sep()
    text("Open chainlog in Terminal")
    text("Open sessions in", chevron=True)
    text("Open at login")
    text("Refresh now", "⌘R")
    sep()
    text("Quit Chainlog", "⌘Q")
    y += 8
    n = d["counts"]["unsaved"]
    svg_ = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'font-family="-apple-system, BlinkMacSystemFont, \'SF Pro Text\', \'Helvetica Neue\', Arial, sans-serif">' % (W, y, W, y),
            '<rect width="100%" height="100%" rx="12" fill="#0d1117"/>',
            '<rect width="100%" height="34" rx="12" fill="#3a3a3f"/><rect y="20" width="100%" height="14" fill="#3a3a3f"/>',
            # the menu bar item: a chain-link icon and the number of unsaved sessions
            '<g fill="none" stroke="%s" stroke-width="1.7" stroke-linecap="round"><rect x="%d" y="11" width="11" height="8" rx="4"/>'
            '<rect x="%d" y="15" width="11" height="8" rx="4"/></g>' % (TEXT, W - 70, W - 63),
            '<text x="%d" y="23" fill="%s" font-size="13" font-weight="500">%d</text>' % (W - 46, TEXT, n),
            '<rect x="%d" y="34" width="%d" height="%d" rx="9" fill="%s" stroke="%s"/>' % (6, W - 12, y - 40, BODY, LINE)]
    # shift the rows right by the 6px body inset so text and lines sit inside the rounded box
    svg_.append('<g transform="translate(6 0)">' + "".join(out).replace('x2="%d"' % W, 'x2="%d"' % (W - 12)) + "</g>")
    svg_.append("</svg>")
    path.write_text("\n".join(svg_) + "\n")


def main():
    global PREVIEW
    if "--preview" in sys.argv:
        PREVIEW = sys.argv[sys.argv.index("--preview") + 1]
        Path(PREVIEW).mkdir(parents=True, exist_ok=True)
    os.environ["COLUMNS"] = str(COLS)
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chainlog-shots-") as tmp:
        demo = Demo(tmp)
        build(demo)
        run(demo, ["scan"])  # first run: records the install time
        cfg = json.loads((demo.data / "config.json").read_text())
        cfg["installed_at"] = int(NOW - 4 * 86400)
        cfg["unsaved"]["lookback"] = 4 * 86400
        (demo.data / "config.json").write_text(json.dumps(cfg))
        # earlier rounds were saved on the days they ran
        run(demo, ["save", "--id", "3f9a1c2e", "r1: checkout with saved cards OK", "-s", "pass"])
        run(demo, ["save", "--id", "a01c77d4", "r2: partial refund + coupon -> HTTP 500", "-s", "fail"])
        run(demo, ["save", "--id", "01a2b3c4", "r3: refunds fixed; rounding edge case left", "-s", "wip"])

        hero = ("$ claude\n\x1b[2m  … you test the coupon rounding fix, then type /exit …\x1b[0m\n"
                + run(demo, ["_nudge", "--shell-pid", "4242"])
                + prompt('chainlog save "r4: rounding fixed, all 14 refund cases pass" -s pass')
                + run(demo, ["save", "r4: rounding fixed, all 14 refund cases pass", "-s", "pass"]))
        svg(hero, "after /exit — one command saves it", OUT / "save.svg")
        saved_line = [l for l in hero.splitlines() if "saved" in l and "✓" in l][0]
        found = run(demo, ["4321"]).splitlines()
        story = (prompt('chainlog save "r4: rounding fixed, all 14 refund cases pass" -s pass') + saved_line + "\n\n"
                 + "\x1b[2m# … next week …\x1b[0m\n" + prompt("chainlog 4321") + "\n".join(found[:-1]) + "\n\n"
                 + "\x1b[1m› \x1b[0mType a number to open it, or words to search (Enter = quit): \x1b[1m1\x1b[0m\n"
                 + "\x1b[2m→ Claude Code opens in ~/code/shop, exactly where you stopped\x1b[0m\n")
        svg(story, "save it · find it · continue it", OUT / "hero.svg")

        # in a real terminal the list ends with "type a number to open it" (here: Enter = quit)
        svg(prompt("chainlog") + run(demo, [], tty=True, answers=[""]), "chainlog — your sessions", OUT / "list.svg")
        svg(prompt("chainlog 4321") + run(demo, ["4321"], tty=True, answers=[""]),
            "find by PR number, word, branch or folder", OUT / "find.svg")
        svg(prompt("chainlog chain 4321") + run(demo, ["chain", "4321"], tty=True, answers=[""]),
            "one PR, every round, both tools", OUT / "chain.svg")
        svg(prompt("chainlog next 4321 --print") + run(demo, ["next", "4321", "--print"])
            + prompt("chainlog next 4321") + "\x1b[2m→ cd ~/code/shop && claude --resume 9b8c7d6e-6666-…   (opens the session)\x1b[0m\n",
            "resume the newest round", OUT / "next.svg")
        svg(prompt("chainlog show 4321/2") + run(demo, ["show", "4321/2"]), "everything about one session", OUT / "show.svg")

        # two sessions ended in the same folder, nothing else to tell them apart -> it asks
        demo.claude("e1e1e1e1-7777-4a00-8000-000000000007", demo.tracker, NOW - 900, NOW - 300,
                    "Tracker CSV export", "test the CSV export with 10k rows", "Export finishes in 4 s; the date column is UTC.")
        demo.claude("f2f2f2f2-8888-4a00-8000-000000000008", demo.tracker, NOW - 800, NOW - 120,
                    "Tracker import validation", "try importing a CSV with a bad header",
                    "Bad headers are rejected with a clear message.")
        svg(prompt('chainlog save "export OK"') + run(demo, ["save", "export OK"], tty=True, answers=["2"],
                                                      cwd=demo.tracker, ppid=9999),
            "not sure which session? it asks", OUT / "picker.svg")

        svg(prompt("chainlog review") + run(demo, ["review"], tty=True,
                                             answers=["s", "sprint item 342: filters OK, toggle bug -s fail", "q"]),
            "triage sessions you forgot to save", OUT / "review.svg")

        menubar_svg(run(demo, ["_menu"]), OUT / "menubar.svg")
        svg(prompt("chainlog doctor") + run(demo, ["doctor"]), "health check", OUT / "doctor.svg")
        svg(prompt("chainlog setup policy") + run(demo, ["setup", "policy"], tty=True, answers=[""]),
            "choose what happens to forgotten sessions", OUT / "policy.svg")
    print("wrote", ", ".join(sorted(p.name for p in OUT.glob("*.svg"))))


if __name__ == "__main__":
    main()
