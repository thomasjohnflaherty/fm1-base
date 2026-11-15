#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""tools/build.py pool_used: the pool's use that the build checks against its headroom rule, for both memory maps, with
and without .pool_text (the symbols as the linker sets them; addresses from fm1-hapax u255's builds and device/*.ld).
Run from the repo root: python3 tests/build_pool_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build  # noqa: E402

CASES = [
    # (name, symbols, expected pool use)
    ("app.ld, .pool_text 14,020 B + 28 B pad", dict(_pt_start=0x01C20000, _pt_end=0x01C236C4, _pool_start=0x01C236E0,
                                                    _pool_end=0x01C6F844), 0x01C6F844 - 0x01C20000),
    ("app.ld, no .pool_text", dict(_pt_start=0x01C20000, _pt_end=0x01C20000, _pool_start=0x01C20000,
                                   _pool_end=0x01C6C164), 0x01C6C164 - 0x01C20000),
    ("app_wifi.ld (no .pool_text: its symbols at the end of .ram_text)",
     dict(_pt_start=0x01C05F00, _pt_end=0x01C05F00, _pool_start=0x01C28000, _pool_end=0x01C70000), 0x01C70000 - 0x01C28000),
    ("an image from before .pool_text (no _pt_ symbols)", dict(_pool_start=0x01C20000, _pool_end=0x01C6C164),
     0x01C6C164 - 0x01C20000),
]
bad = 0
for name, syms, want in CASES:
    got = build.pool_used(lambda n, s=syms: s.get(n, 0))
    ok = got == want
    bad += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got} B (want {want})")
print(f"build_pool_test: {len(CASES) - bad}/{len(CASES)}")
sys.exit(1 if bad else 0)
