"""The LLM tool call sensor.

Home Assistant adds each tool result to a conversation's chat log the moment the tool returns, seconds before the
answer is finished. This sensor follows every chat log inside Home Assistant (nothing leaves it but the sensor's
state) and, for each tool result, shows:

- state: the name of the LLM API the tool belongs to, as the conversation agent's settings list it, such as
  "Voice Satellite: Image Search" or "Assist".
- display: whether the result has something a screen shows: pictures, videos, a forecast or a quote. The rules are
  Kiosk Satellite's for its result panels.
- words: what was said to start the turn, so a speaker can tell its own turn from another's.
- time: when the result came back, so every result is an update, even the same one twice.

words and time are left out of history.
"""

from __future__ import annotations

import asyncio
from typing import Any

import slugify as unicode_slug

from homeassistant.components.conversation.chat_log import (
    DATA_CHAT_LOGS,
    async_subscribe_chat_logs,
    current_chat_log,
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    """Add the sensor."""
    add([ToolCallSensor(entry)])


def has_display(tool: str, data: Any) -> bool:
    """Whether a tool result has something Kiosk Satellite shows in a panel, by its rules."""
    if not isinstance(data, dict):
        return False
    error = data.get("error")
    if error is not None and error is not False:
        return False
    if tool.endswith("get_weather_forecast") and data.get("forecast") is not None:
        return True
    if "financial-data__get_financial_data" in tool and data.get("query_type") is not None:
        return True
    for item in data.get("results") or []:
        if isinstance(item, dict) and (item.get("video_id") is not None or item.get("image_url") is not None):
            return True
    featured = data.get("featured_image")
    return isinstance(featured, str) and featured != ""


def _get(item: Any, key: str) -> Any:
    """A field of a chat log entry, whether a dict or an object (tool calls are ToolInput objects in-process)."""
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _last_words(content: list[Any]) -> str:
    for item in reversed(content or []):
        if _get(item, "role") == "user" and isinstance(_get(item, "content"), str):
            return _get(item, "content")
    return ""


class ToolCallSensor(SensorEntity):
    """The LLM API of the latest tool to return."""

    _attr_name = "LLM tool call"
    _attr_icon = "mdi:tools"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"words", "time"})

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_tool_call"
        # Per conversation: the words that began its latest turn, and its tool calls without a result yet.
        self._words: dict[str, str] = {}
        self._calls: dict[str, dict[str, str]] = {}
        # Tool name to API name, per merged API, as looking it up builds each API's tools.
        self._names: dict[tuple[str, str], str] = {}
        self._order = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        """Follow every chat log."""
        self.async_on_remove(async_subscribe_chat_logs(self.hass, self._on_chat_log))

    @callback
    def _on_chat_log(self, conversation_id: str, event_type: str, data: dict[str, Any]) -> None:
        if event_type == "created":
            self._words[conversation_id] = _last_words(_get(data.get("chat_log") or {}, "content"))
            self._calls[conversation_id] = {}
        elif event_type == "deleted":
            self._words.pop(conversation_id, None)
            self._calls.pop(conversation_id, None)
        elif event_type == "content_added":
            self._on_content(conversation_id, data.get("content") or {})

    @callback
    def _on_content(self, conversation_id: str, content: Any) -> None:
        role = _get(content, "role")
        calls = self._calls.setdefault(conversation_id, {})
        if role == "user":
            self._words[conversation_id] = _get(content, "content") or ""
            calls.clear()
        elif role == "assistant":
            for call in _get(content, "tool_calls") or []:
                if _get(call, "external"):
                    continue
                calls[str(_get(call, "id"))] = str(_get(call, "tool_name") or _get(call, "name") or "")
        elif role == "tool_result":
            call_id = str(_get(content, "tool_call_id"))
            tool = str(_get(content, "tool_name") or calls.get(call_id, ""))
            calls.pop(call_id, None)
            data = _get(content, "tool_result")
            if data is None:
                data = _get(_get(content, "result") or {}, "data")
            words = self._words.get(conversation_id, "")
            time = dt_util.utcnow().isoformat(timespec="milliseconds")
            # The turn's chat log, for the APIs its tools came from. While the turn runs it's the current one
            # (Home Assistant only stores it once the turn ends).
            chat_log = current_chat_log.get()
            if chat_log is None or chat_log.conversation_id != conversation_id:
                chat_log = (self.hass.data.get(DATA_CHAT_LOGS) or {}).get(conversation_id)
            apis = getattr(chat_log, "llm_api", None)
            self.hass.async_create_task(
                self._publish(apis, tool, has_display(tool, data), words, time),
                eager_start=True,
            )

    async def _publish(self, instance: Any, tool: str, display: bool, words: str, time: str) -> None:
        # Results are published in the order they came back, even when an API name takes a moment to look up.
        async with self._order:
            name = await self._api_name(instance, tool)
            self._attr_native_value = name[:255]
            self._attr_extra_state_attributes = {"display": display, "words": words, "time": time}
            self.async_write_ha_state()

    async def _api_name(self, instance: Any, tool: str) -> str:
        """The name of the LLM API a tool comes from, as the conversation agent's settings list it."""
        if instance is None:
            return tool
        api = instance.api
        apis = list(getattr(api, "llm_apis", None) or [api])
        if len(apis) == 1:
            return apis[0].name
        # Merged APIs namespace their tools as "<api>__<tool>", the API slugged from its name or its id (Home
        # Assistant versions differ, and some only namespace tools that share a name). Tools can have "__" in their
        # own names too, such as Assist's "intent__HassTurnOn", so the name is first looked for as it is.
        key = (api.id, tool)
        if key in self._names:
            return self._names[key]
        tools: dict[str, set[str]] = {}
        for candidate in apis:
            try:
                built = await candidate.async_get_api_instance(instance.llm_context)
            except Exception:  # noqa: BLE001 - an API that can't build its tools here just isn't the one
                continue
            tools[candidate.id] = {t.name for t in built.tools}
        namespace, _, rest = tool.partition("__")
        found = next((c for c in apis if tool in tools.get(c.id, ())), None) or next(
            (
                c
                for c in apis
                if rest in tools.get(c.id, ())
                and namespace in (unicode_slug.slugify(c.id), unicode_slug.slugify(c.name))
            ),
            None,
        )
        if found is None:
            return tool
        self._names[key] = found.name
        return found.name
