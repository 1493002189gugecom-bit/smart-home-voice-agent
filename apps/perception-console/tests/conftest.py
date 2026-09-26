"""Make the console sources importable from its tests.

The console imports `proxy` from a sibling source file. This path also keeps
the tests runnable from the repository root. Status tests load the console's
`main.py` under a unique name because vision-service has its own `main.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
