#!/usr/bin/env python3
"""Tester wrapper: the REAL demo_run.run()/main entry with a quick two-step table (windows 3 s) instead of the 40 s ones."""
import importlib.util, os, sys
import os
from pathlib import Path
REPO = Path(os.environ.get("T17_REPO", "/home/sv-rpi5/Projects/Second-Vision/worktrees/kenth"))
spec = importlib.util.spec_from_file_location("demo_run", REPO / "docs/testing/tools/demo_run.py")
demo = importlib.util.module_from_spec(spec); sys.modules["demo_run"] = demo; spec.loader.exec_module(demo)
E, A, S = demo.Expect, demo.Action, demo.Step
demo.STEPS = [S("one", "Quick one", "say one", [A("Do one.", [E(r"^\[TTS\] 'tick'", 3, "a tick")])]),
              S("two", "Quick two", "say two", [A("Do two.", [E(r"^\[TTS\] 'tick'", 3, "a tick")])])]
if os.environ.get("WRAP_RAISE"):
    import time
    def boom(*a, **k):
        time.sleep(float(os.environ.get("WRAP_RAISE_AFTER", "0")))
        raise RuntimeError("boom in watch")
    demo.watch = boom
sys.exit(demo.run(sys.argv[1:]))
