"""Under mutation testing, the package under test is mutmut's copy (`Q-02`).

mutmut runs pytest from `mutants/`, beside a mutated copy of `meridian_core`, but the
workspace's editable install puts the real package on `sys.path` first, so every test ran the
unmutated code and mutmut reported that no test covered any mutant. Imported by `conftest.py`
before anything imports `meridian_core`. Outside `mutants/` it does nothing.
See docs/guides/testing.md.
"""

from __future__ import annotations

import os
import sys

if os.path.basename(os.getcwd()) == "mutants":
    sys.path.insert(0, os.path.join(os.getcwd(), "packages", "meridian_core"))
