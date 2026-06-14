"""WhatsApp channel implementation using a pure-Python Neonize client."""

import asyncio
from collections import OrderedDict
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import OwnerConfig, WhatsAppConfig


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel backed by Neonize instead of the old Node.js bridge.

    Neonize wraps whatsmeow and connects directly to WhatsApp Web from Python. On
    a fresh session it emits a QR payload, which this channel renders in the
    console so the user can scan it from WhatsApp → Linked devices.
    """

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus, owner: OwnerConfig | None = None):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self.owner = owner or OwnerConfig()
        self.owner_identifiers = {self.owner.phone, self.owner.normalized_phone}
        self._client: Any | None = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._chat_refs: dict[str, Any] = {}
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Start the WhatsApp channel and print a QR code if login is needed."""
        self._running = True
        self._stop_event.clear()

        try:
            await self._start_neonize()
        except ImportError as e:
            logger.error(
                "WhatsApp requires the Python Neonize dependencies. Install them with: "
                "python -m pip install 'nanobot-ai[whatsapp]' or python -m pip install neonize segno"
            )
            logger.debug("Neonize import error: {}", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("WhatsApp channel stopped with error: {}", e)
        finally:
            self._connected = False

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False
        self._stop_event.set()
        if self._client:
            stop = getattr(self._client, "stop", None)
            if stop:
                result = stop()
                if asyncio.iscoroutine(result):
                    await result
            self._client = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._client or not self._connected:
            logger.warning("WhatsApp is not connected")
            return

        try:
            to = self._chat_refs.get(msg.chat_id) or self._build_jid(msg.chat_id)
            if msg.metadata.get("whatsapp_type") == "typing":
                await self._send_typing(to)
                return
            if msg.metadata.get("contact"):
                await self._send_contact(to, msg.content, msg.metadata["contact"])
                return
            if self.config.send_typing:
                await self._send_typing(to)
            await self._send_text(to, msg.content)
            await self._send_paused(to)
        except Exception as e:
            logger.error("Error sending WhatsApp message: {}", e)

    async def _start_neonize(self) -> None:
        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv, QREv

        db_path = Path(self.config.session_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        client = NewAClient(str(db_path))
        self._client = client

        @client.event(ConnectedEv)
        async def on_connected(_: Any, __: Any) -> None:
            self._connected = True
            logger.info("✅ Connected to WhatsApp")

        @client.event(QREv)
        async def on_qr(_: Any, ev: Any) -> None:
            code = self._extract_qr_code(ev)
            if code:
                self._print_terminal_qr(code)

        @client.event(PairStatusEv)
        async def on_pair_status(_: Any, ev: Any) -> None:
            logger.info("WhatsApp linked as {}", getattr(getattr(ev, "ID", None), "User", "unknown"))

        @client.event(MessageEv)
        async def on_message(_: Any, message: Any) -> None:
            await self._handle_neonize_message(message)

        await client.connect()
        idle = getattr(client, "idle", None)
        if idle:
            await idle()
        else:
            await self._stop_event.wait()

    async def _handle_neonize_message(self, message: Any) -> None:
        info = getattr(message, "Info", None)
        source = getattr(info, "MessageSource", None)
        msg = getattr(message, "Message", None)
        message_id = str(getattr(info, "ID", ""))
        if message_id:
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

        chat = getattr(source, "Chat", None)
        sender = getattr(source, "Sender", None) or chat
        chat_id = str(chat or sender or "")
        sender_id = str(sender or chat or "").split("@")[0]
        is_group = chat_id.endswith("@g.us")
        content = self._extract_message_content(msg)
        if not content:
            return
        self._chat_refs[chat_id] = chat

        if content == "[Voice Message]":
            logger.info("Voice message received from {}, but transcription is not yet supported.", sender_id)
            content = "[Voice Message: Transcription not available for WhatsApp yet]"

        if await self._maybe_answer_owner_question(chat_id, content):
            return
        if is_group and self.config.require_name_in_groups and not self._is_addressed(content):
            logger.debug("Ignoring WhatsApp group message not addressed to bot")
            return

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata={"message_id": message_id, "is_group": is_group},
        )

    def _extract_message_content(self, msg: Any) -> str | None:
        if not msg:
            return None
        if getattr(msg, "conversation", None):
            return msg.conversation
        extended = getattr(msg, "extendedTextMessage", None)
        if getattr(extended, "text", None):
            return extended.text
        for field, label in (("imageMessage", "Image"), ("videoMessage", "Video"), ("documentMessage", "Document")):
            media = getattr(msg, field, None)
            if media:
                caption = getattr(media, "caption", "")
                return f"[{label}] {caption}".strip()
        if getattr(msg, "audioMessage", None):
            return "[Voice Message]"
        return None

    def _extract_qr_code(self, ev: Any) -> str | None:
        codes = getattr(ev, "Codes", None) or getattr(ev, "codes", None) or ev
        if isinstance(codes, (list, tuple)):
            return str(codes[0]) if codes else None
        return str(codes) if codes else None

    def _print_terminal_qr(self, code: str) -> None:
        logger.info("WhatsApp login QR code generated. Scan it in WhatsApp → Linked devices → Link a device.")
        try:
            import segno

            segno.make(code).terminal(compact=True)
        except Exception:
            logger.warning("Could not render terminal QR code; raw QR payload follows:")
            print(code)

    def _build_jid(self, chat_id: str) -> Any:
        from neonize.utils import build_jid

        if "@" in chat_id:
            return build_jid(chat_id)
        return build_jid(f"{chat_id}@s.whatsapp.net")

    async def _send_text(self, to: Any, text: str) -> None:
        result = self._client.send_message(to, text)
        if asyncio.iscoroutine(result):
            await result

    async def _send_typing(self, to: Any) -> None:
        sender = getattr(self._client, "send_chat_presence", None) or getattr(self._client, "send_presence", None)
        if sender:
            result = sender(to, "composing")
            if asyncio.iscoroutine(result):
                await result

    async def _send_paused(self, to: Any) -> None:
        sender = getattr(self._client, "send_chat_presence", None) or getattr(self._client, "send_presence", None)
        if sender:
            result = sender(to, "paused")
            if asyncio.iscoroutine(result):
                await result

    async def _send_contact(self, to: Any, text: str, contact: dict[str, str]) -> None:
        # Neonize contact-card method names have changed between releases, so send
        # a plain, useful fallback instead of failing owner-contact replies.
        phone = str(contact.get("phone", ""))
        name = contact.get("name", "Contact")
        await self._send_text(to, f"{text}\n{name}: {phone}".strip())

    def _is_addressed(self, content: str) -> bool:
        text = content.lower()
        return "@" in content or any(name.lower() in text for name in self.config.respond_to_names)

    async def _maybe_answer_owner_question(self, chat_id: str, content: str) -> bool:
        text = content.lower()
        if "owner" not in text:
            return False
        await self._send_contact(
            self._chat_refs.get(chat_id) or self._build_jid(chat_id),
            f"My owner is {self.owner.name}.",
            {"name": self.owner.name, "phone": self.owner.normalized_phone},
        )
        return True
