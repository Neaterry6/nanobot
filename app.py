"""Pterodactyl-friendly launcher for nanobot.

Many Python Pterodactyl eggs run ``python /home/container/${PY_FILE}`` and
set ``PY_FILE`` to ``app.py`` by default. Keeping this launcher in the
project root lets those panels start nanobot without a custom Python file.

When no CLI args are provided, this launcher assumes the hosted panel should
run the WhatsApp bot: it enables the WhatsApp channel in the user config,
starts the Node bridge so it can ask for a phone number / pairing code, then
starts the nanobot gateway.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import sys

from nanobot.cli.commands import _get_bridge_dir, app
from nanobot.config.loader import load_config, save_config


def _prepare_default_whatsapp_gateway() -> None:
    """Enable WhatsApp and start the bridge for panel default startup."""
    if os.environ.get("NANOBOT_AUTO_WHATSAPP", "1").lower() in {"0", "false", "no"}:
        return

    config = load_config()
    if not config.channels.whatsapp.enabled:
        config.channels.whatsapp.enabled = True
        save_config(config)
        print("✓ Enabled WhatsApp channel in ~/.nanobot/config.json", flush=True)

    bridge_dir = _get_bridge_dir()
    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token

    bridge = subprocess.Popen(["npm", "start"], cwd=bridge_dir, env=env)
    atexit.register(lambda: bridge.poll() is None and bridge.terminate())


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        _prepare_default_whatsapp_gateway()
        args = ["gateway"]
    app(args=args)
