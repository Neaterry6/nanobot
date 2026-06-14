"""Pterodactyl-friendly launcher for nanobot.

Many Python Pterodactyl eggs run ``python /home/container/${PY_FILE}`` and
set ``PY_FILE`` to ``app.py`` by default. Keeping this launcher in the
project root lets those panels start nanobot without a custom Python file.

When no CLI args are provided, this launcher assumes the hosted panel should
run the WhatsApp bot when Node.js/npm is available: it enables the WhatsApp
channel, starts the Node bridge so it can ask for a phone number / pairing
code, then starts the nanobot gateway. If npm is missing, startup continues
without crashing and prints the exact fix needed.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys

from nanobot.cli.commands import _get_bridge_dir, app
from nanobot.config.loader import load_config, save_config


def _prepare_default_whatsapp_gateway() -> None:
    """Enable WhatsApp and start the bridge for panel default startup."""
    if os.environ.get("NANOBOT_AUTO_WHATSAPP", "1").lower() in {"0", "false", "no"}:
        return

    if not shutil.which("npm"):
        print(
            "⚠️ WhatsApp bridge not started: npm was not found. "
            "Install Node.js 20+ on the panel/startup command, then restart. "
            "Nanobot gateway will keep starting instead of crashing.",
            flush=True,
        )
        return

    config = load_config()
    if not config.channels.whatsapp.enabled:
        config.channels.whatsapp.enabled = True
        save_config(config)
        print("✓ Enabled WhatsApp channel in ~/.nanobot/config.json", flush=True)

    try:
        bridge_dir = _get_bridge_dir()
        env = {**os.environ}
        if config.channels.whatsapp.bridge_token:
            env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

        bridge = subprocess.Popen(["npm", "start"], cwd=bridge_dir, env=env)
        atexit.register(lambda: bridge.poll() is None and bridge.terminate())
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"⚠️ WhatsApp bridge failed to start: {exc}. Nanobot gateway will keep running.", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        _prepare_default_whatsapp_gateway()
        args = ["gateway"]
    app(args=args)
