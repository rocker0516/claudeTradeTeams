"""Entry point for `python -m trading`.

Delegates to `trading.runtime.main:run`. See that module's docstring
for usage and platform notes.
"""

import sys

from .runtime.main import run

if __name__ == "__main__":
    sys.exit(run())
