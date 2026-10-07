"""Telegram, the transport (task P5-07, spec §13.3, §11.11).

What the digest, the alerts and the inbound bot (`worker/bot.py`) share. `poll` reads
updates and decides nothing about them. The token comes from the environment, never the
database, and no token is a supported state. See docs/features/operations.md#telegram.
"""

from __future__ import annotations

import os

import httpx

from meridian_core.logging import get_logger

log = get_logger(__name__)

API = "https://api.telegram.org"
DEFAULT_TIMEOUT_S = 15.0

#: Telegram refuses a message over 4096 characters. A digest that grew past it
#: would fail entirely rather than arrive shortened, which is the wrong failure
#: for the channel that is supposed to tell you things are wrong.
MAX_MESSAGE = 4096

#: How long Telegram holds a `getUpdates` request open with nothing to say.
#: Long enough that a quiet bot makes a handful of requests an hour, short
#: enough that a restart is noticed within the minute.
LONG_POLL_S = 50


class Telegram:
    """A bot that can send to exactly one chat."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout_s
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_env(cls) -> Telegram | None:
        """Both or neither.

        A token without a chat id has nowhere to send, and a chat id without a
        token cannot. Returning None for either keeps "not configured" a single
        state rather than two half-configured ones that fail differently.
        """
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (token and chat):
            return None
        return cls(token, chat)

    @property
    def chat_id(self) -> str:
        """The one chat this bot talks to, and the only one it takes orders from."""
        return self._chat_id

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def poll(self, *, offset: int | None, timeout_s: int = LONG_POLL_S) -> list[dict] | None:
        """Wait for messages, and return them. Never raises.

        `[]` is "nothing was said"; `None` is "could not ask", which a loop must not
        treat as an empty answer. `offset` (`last_update_id + 1`) acknowledges earlier
        updates. Long polling: no inbound port is needed.
        """
        try:
            response = await self._http().get(
                f"{API}/bot{self._token}/getUpdates",
                params={
                    "timeout": timeout_s,
                    # Only messages. Telegram will otherwise deliver edits,
                    # channel posts and callback queries, and an *edited*
                    # command is a command somebody can rewrite after the fact.
                    "allowed_updates": '["message"]',
                    **({"offset": offset} if offset is not None else {}),
                },
                # Past the long poll: the server holds the request open for
                # `timeout_s`, so a client timeout below it would abort every
                # quiet interval and look like a network fault.
                timeout=timeout_s + self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The token is in the URL, so only the type is logged.
            log.warning("telegram poll failed", extra={"reason": type(exc).__name__})
            return None

        body = response.json()
        if not body.get("ok"):
            # `ok: false` with a 200 is how Telegram reports a second poller on
            # the same token. Retrying instantly would make two loops fight.
            log.warning(
                "telegram refused the poll", extra={"reason": str(body.get("description"))[:80]}
            )
            return None
        return list(body.get("result") or [])

    async def send(self, text: str) -> bool:
        """Send one message. Returns whether it arrived.

        Never raises: findings are already in `notifications`, so a failed send loses
        only the delivery.
        """
        body = text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 20].rstrip() + "\n…"
        try:
            response = await self._http().post(
                f"{API}/bot{self._token}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": body,
                    # Markdown is off: an unbalanced asterisk in a crawled title would
                    # make Telegram reject the whole message.
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            # The token is never logged, here or anywhere: it is in the URL, so
            # only the exception *type* is recorded rather than its message.
            log.warning("telegram send failed", extra={"reason": type(exc).__name__})
            return False
