"""Pterodactyl-friendly launcher for nanobot.

Many Python Pterodactyl eggs run ``python /home/container/${PY_FILE}`` and
set ``PY_FILE`` to ``app.py`` by default. Keeping this tiny launcher in the
project root lets those panels start nanobot without a custom Python file.
"""

from __future__ import annotations

import sys

from nanobot.cli.commands import app


if __name__ == "__main__":
    app(args=sys.argv[1:] or ["gateway"])
