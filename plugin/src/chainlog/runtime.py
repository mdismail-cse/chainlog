"""Runtime: the one place that touches the environment, the clock, processes and the terminal.

Core modules receive a Runtime instead of reading os.environ or calling subprocess, so tests can
inject a fake HOME, a fixed clock, a fake process table and a fake command runner.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


class Runtime:
    def __init__(self, env=None, home=None, clock=None, run=None, proctable=None, ppid=None,
                 stdin=None, stdout=None, tty=None, tty_name=None):
        self.env = dict(os.environ) if env is None else dict(env)
        self.home = Path(home or self.env.get("HOME") or os.path.expanduser("~"))
        self._clock = clock or time.time
        self._run = run or _default_run
        self._proctable = proctable
        self._ppid = ppid
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._tty = tty
        self._tty_name = tty_name

    # --- locations -------------------------------------------------------------------------
    @property
    def data_home(self) -> Path:
        if self.env.get("CHAINLOG_HOME"):
            return Path(self.env["CHAINLOG_HOME"])
        base = self.env.get("XDG_DATA_HOME") or str(self.home / ".local" / "share")
        return Path(base) / "chainlog"

    @property
    def claude_dir(self) -> Path:
        return Path(self.env.get("CLAUDE_CONFIG_DIR") or self.home / ".claude")

    @property
    def codex_dir(self) -> Path:
        return Path(self.env.get("CODEX_HOME") or self.home / ".codex")

    # --- time / processes ------------------------------------------------------------------
    def now(self) -> float:
        return self._clock()

    def ppid(self) -> int:
        return self._ppid if self._ppid is not None else os.getppid()

    def run(self, argv, timeout=5.0, input=None, env=None, cwd=None):
        """Run a command; returns (returncode, stdout, stderr). Never raises for a missing binary."""
        return self._run(argv, timeout=timeout, input=input, env=env, cwd=cwd)

    def proctable(self) -> dict:
        """pid -> (ppid, comm). One `ps` call, cached for the lifetime of this Runtime."""
        if self._proctable is None:
            table = {}
            rc, out, _ = self.run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "comm="], timeout=3)
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split(None, 2)
                    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                        table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
            self._proctable = table
        return self._proctable

    def ancestors(self, pid=None, limit=12) -> list:
        """[pid, parent, grandparent, ...] starting at `pid` (default: our parent)."""
        table = self.proctable()
        p = self.ppid() if pid is None else pid
        out = []
        while p and p > 1 and len(out) < limit:
            out.append(p)
            p = table.get(p, (0, ""))[0]
        return out

    def pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    # --- terminal --------------------------------------------------------------------------
    def isatty(self) -> bool:
        if self._tty is not None:
            return self._tty
        try:
            return self.stdin.isatty() and self.stdout.isatty()
        except (AttributeError, ValueError):
            return False

    def tty_name(self) -> str:
        """tty of the calling shell as `ps -o tty=` prints it ('ttys001' on macOS, 'pts/3' on Linux),
        '' when there is none."""
        if self._tty_name is not None:
            return self._tty_name
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            try:
                name = os.ttyname(stream.fileno())
                return name[len("/dev/"):] if name.startswith("/dev/") else ""
            except (OSError, AttributeError, ValueError):
                continue
        return ""

    def color(self) -> bool:
        if self.env.get("NO_COLOR"):
            return False
        if self.env.get("CHAINLOG_COLOR") == "always" or self.env.get("FORCE_COLOR"):
            return True  # e.g. when piping into a pager or capturing screenshots
        return self.isatty() and self.env.get("TERM") != "dumb"


def _default_run(argv, timeout=5.0, input=None, env=None, cwd=None):
    """Run in its own process group, so a timeout also stops whatever the command started
    (e.g. the AI summarizer's helpers)."""
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd,
                             start_new_session=True)
    except FileNotFoundError:
        return 127, "", "not found: %s" % argv[0]
    except OSError as e:
        return 126, "", str(e)
    try:
        out, err = p.communicate(input=input, timeout=timeout)
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, 9)
        except OSError:
            p.kill()
        p.communicate()
        return 124, "", "timeout after %ss" % timeout
