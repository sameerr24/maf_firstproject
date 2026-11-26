"""Minimal Telegram Bot API client (https://core.telegram.org/bots/api)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests


class TelegramError(RuntimeError):
    def __init__(self, status: int, description: str):
        super().__init__(f"Telegram API error {status}: {description}")
        self.status = status
        self.description = description


class TelegramBlocked(TelegramError):
    """The user blocked the bot or never started it (HTTP 403)."""


class TelegramClient:
    def __init__(self, token: str, timeout: float = 10.0):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self._base = f"https://api.telegram.org/bot{token}/"
        self._timeout = timeout
        self._http = requests.Session()        # keep-alive: one TLS handshake, not one per call

    def reset(self) -> None:
        """Drop pooled connections, e.g. after a network drop or the laptop waking from sleep;
        otherwise requests can keep waiting on dead sockets."""
        self._http.close()
        self._http = requests.Session()

    def call(self, method: str, _http_timeout: float | None = None, _files: dict | None = None,
             **params: Any) -> Any:
        """POST a Bot API method. `_http_timeout` is the HTTP timeout; `_files` uploads files
        (multipart); any other keyword (including Telegram's own `timeout` for long polling) is
        sent as a parameter."""
        params = {k: v for k, v in params.items() if v is not None}
        if _files:      # multipart form: nested values (reply_markup) go as JSON strings
            form = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}
            resp = self._http.post(self._base + method, data=form, files=_files,
                                   timeout=_http_timeout or self._timeout)
        else:
            resp = self._http.post(self._base + method, json=params, timeout=_http_timeout or self._timeout)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 403:
            raise TelegramBlocked(403, data.get("description", "forbidden"))
        if not data.get("ok"):
            raise TelegramError(resp.status_code, data.get("description", resp.text[:200]))
        return data["result"]

    def get_me(self) -> dict:
        return self.call("getMe")

    def get_updates(self, offset: int | None, timeout: int = 20) -> list[dict]:
        return self.call("getUpdates", _http_timeout=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         reply_markup=reply_markup)

    def send_photo(self, chat_id: int, photo: Path | str, caption: str, reply_markup: dict | None = None) -> dict:
        """A photo with an HTML caption (at most 1024 characters). `photo` is a local file to
        upload, or the file_id Telegram returned for an earlier upload (no re-upload)."""
        params = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML", "reply_markup": reply_markup}
        if isinstance(photo, Path):
            with photo.open("rb") as f:
                return self.call("sendPhoto", _http_timeout=30, _files={"photo": (photo.name, f, "image/jpeg")},
                                 **params)
        return self.call("sendPhoto", photo=photo, **params)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_query_id, text=text)

    def remove_buttons(self, chat_id: int, message_id: int) -> None:
        self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                  reply_markup={"inline_keyboard": []})
