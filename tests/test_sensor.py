"""LLM Tool Call Helper, in a real Home Assistant: conversations run through the chat log with LLM APIs whose tools
return what Voice Satellite LLM Tools returns."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.components.conversation import AssistantContent, ConversationInput, async_get_chat_log
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session, llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.llm_tool_call_helper.const import DOMAIN
from custom_components.llm_tool_call_helper.sensor import ToolCallSensor, has_display

ENTITY = "sensor.llm_tool_call"


class FakeTool(llm.Tool):
    def __init__(self, name: str, result: dict[str, Any]) -> None:
        self.name = name
        self.description = name
        self.integration = "test"
        self._result = result

    async def async_call(self, hass, tool_input, llm_context):
        return llm.ToolResult(data=self._result, error="error" in self._result)


class FakeAPI(llm.API):
    def __init__(self, hass, api_id: str, name: str, tools: list[llm.Tool]) -> None:
        super().__init__(hass=hass, id=api_id, name=name)
        self._tools = tools

    async def async_get_api_instance(self, llm_context):
        return llm.APIInstance(api=self, api_prompt="", llm_context=llm_context, tools=self._tools)


IMAGES = {"results": [{"image_url": "https://example.org/pangolin.jpg", "title": "Pangolin"}], "auto_display": True}
FORECAST = {"range": "tomorrow", "forecast": [{"condition": "rainy", "temperature": 14}]}


def wiki(thumbnail):
    return {"title": "Harrison Ford", "summary": "American actor.", "featured_image": thumbnail}


@pytest.fixture
async def apis(hass: HomeAssistant):
    """The LLM Tools add-on's APIs as the conversation agent's settings list them, and Home Assistant's Assist."""
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "llm", {})
    assert await async_setup_component(hass, "conversation", {})
    registered = {
        "image": FakeAPI(hass, "voice_satellite_llm_tools_image_search", "Voice Satellite: Image Search", [FakeTool("search_images", IMAGES)]),
        "weather": FakeAPI(hass, "voice_satellite_llm_tools_weather", "Voice Satellite: Weather Forecast", [FakeTool("get_weather_forecast", FORECAST)]),
        "wiki": FakeAPI(hass, "voice_satellite_llm_tools_wikipedia", "Voice Satellite: Wikipedia", [FakeTool("search_wikipedia", wiki("https://upload.wikimedia.org/ford.jpg"))]),
        "web": FakeAPI(hass, "voice_satellite_llm_tools_web_search", "Voice Satellite: Web Search", [FakeTool("search_web", {"results": [{"title": "x", "url": "https://example.org"}], "featured_image": None})]),
    }
    for api in registered.values():
        llm.async_register_api(hass, api)
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return registered


async def tool_named(hass, apis, bare):
    """A tool's name as the AI sees it, which Home Assistant may namespace when an agent has several APIs."""
    probe = ConversationInput(text="x", context=Context(), conversation_id=None, device_id=None, satellite_id=None, language="en", agent_id="conversation.test_agent")
    merged = await llm.async_get_api(hass, apis, probe.as_llm_context("test"))
    return next(t.name for t in merged.tools if t.name == bare or t.name.endswith("__" + bare))


async def turn(hass, text, apis, calls, conversation_id=None) -> str:
    """One turn: the words, the AI calling tools, their results, then the answer."""
    user_input = ConversationInput(
        text=text, context=Context(), conversation_id=conversation_id, device_id=None,
        satellite_id=None, language="en", agent_id="conversation.test_agent",
    )
    try:  # Home Assistant 2026.11 asks for the turn's context
        opened = chat_session.async_get_chat_session(hass, conversation_id, context=user_input.context)
    except TypeError:
        opened = chat_session.async_get_chat_session(hass, conversation_id)
    with opened as session, async_get_chat_log(hass, session, user_input) as chat_log:
        await chat_log.async_provide_llm_data(user_input.as_llm_context("test"), apis, None, None)
        content = AssistantContent(
            agent_id="conversation.test_agent",
            tool_calls=[llm.ToolInput(tool_name=name, tool_args=args) for name, args in calls],
        )
        async for _ in chat_log.async_add_assistant_content(content):
            pass
        chat_log.async_add_assistant_content_without_tools(AssistantContent(agent_id="conversation.test_agent", content="Here you go."))
        conversation = session.conversation_id
    await hass.async_block_till_done()
    return conversation


def changes(hass):
    seen = []
    hass.bus.async_listen("state_changed", lambda e: seen.append(e.data["new_state"]) if e.data["entity_id"] == ENTITY else None)
    return seen


async def test_entity_exists(hass, apis):
    state = hass.states.get(ENTITY)
    assert state is not None, "sensor.llm_tool_call is created"
    assert state.state == "unknown"


async def test_one_api(hass, apis):
    seen = changes(hass)
    await turn(hass, "What does a pangolin look like?", ["voice_satellite_llm_tools_image_search"], [("search_images", {"query": "pangolin"})])
    state = hass.states.get(ENTITY)
    assert state.state == "Voice Satellite: Image Search"
    assert state.attributes["display"] is True
    assert state.attributes["words"] == "What does a pangolin look like?"
    assert state.attributes["time"].endswith("+00:00") and "." in state.attributes["time"]
    assert len(seen) == 1, "One update per tool result"


@pytest.mark.parametrize(
    ("tool", "args", "name", "display"),
    [
        ("search_images", {"query": "pangolin"}, "Voice Satellite: Image Search", True),
        ("get_weather_forecast", {"range": "tomorrow"}, "Voice Satellite: Weather Forecast", True),
        ("search_wikipedia", {"query": "Harrison Ford"}, "Voice Satellite: Wikipedia", True),
        ("search_web", {"query": "super bowl"}, "Voice Satellite: Web Search", False),
        ("GetLiveContext", {}, "Assist", False),
        ("GetDateTime", {}, "Assist", False),
    ],
)
async def test_merged_apis(hass, apis, tool, args, name, display):
    """Several APIs on the agent, as the settings list them, and Home Assistant's own."""
    agent = ["assist", *[a.id for a in apis.values()]]
    await turn(hass, "question", agent, [(await tool_named(hass, agent, tool), args)])
    if name == "Assist":
        # Home Assistant's own API: "Assist" up to 2026.10, "Device control" from 2026.11.
        name = next(a.name for a in llm.async_get_apis(hass) if a.id == "assist")
    state = hass.states.get(ENTITY)
    assert (state.state, state.attributes["display"]) == (name, display)


async def test_wikipedia_without_picture(hass, apis):
    llm.async_register_api(hass, FakeAPI(hass, "wiki_bare", "Wiki without pictures", [FakeTool("look_up", wiki(None))]))
    await turn(hass, "tell me about the eiffel tower", ["wiki_bare"], [("look_up", {})])
    assert hass.states.get(ENTITY).attributes["display"] is False


async def test_error(hass, apis):
    llm.async_register_api(hass, FakeAPI(hass, "broken", "Broken Search", [FakeTool("search_images", {"error": "Brave search failed"})]))
    await turn(hass, "show me a cat", ["broken"], [("search_images", {})])
    state = hass.states.get(ENTITY)
    assert (state.state, state.attributes["display"]) == ("Broken Search", False)


async def test_same_name_in_two_apis(hass, apis):
    """Home Assistant namespaces tools that share a name; each still names its own API."""
    llm.async_register_api(hass, FakeAPI(hass, "pictures_one", "Pictures One", [FakeTool("search", IMAGES)]))
    llm.async_register_api(hass, FakeAPI(hass, "pictures_two", "Pictures Two", [FakeTool("search", {"results": []})]))
    probe = ConversationInput(text="x", context=Context(), conversation_id=None, device_id=None, satellite_id=None, language="en", agent_id="conversation.test_agent")
    merged = await llm.async_get_api(hass, ["pictures_one", "pictures_two"], probe.as_llm_context("test"))
    names = sorted(t.name for t in merged.tools)
    for tool in names:
        await turn(hass, "pictures", ["pictures_one", "pictures_two"], [(tool, {})])
        state = hass.states.get(ENTITY)
        assert state.state == ("Pictures One" if tool.startswith("pictures_one") or tool.startswith("pictures-one") else "Pictures Two"), (names, tool, state.state)
        assert state.attributes["display"] is tool.startswith(("pictures_one", "pictures-one"))


async def test_every_result_updates(hass, apis):
    """The same question twice, with the same result, still updates the entity: its time differs."""
    seen = changes(hass)
    for _ in range(2):
        await turn(hass, "What's the weather tomorrow?", ["voice_satellite_llm_tools_weather"], [("get_weather_forecast", {"range": "tomorrow"})])
    assert len(seen) == 2
    assert seen[0].attributes["time"] != seen[1].attributes["time"]


async def test_two_tools_both_published(hass, apis):
    seen = changes(hass)
    agent = ["assist", *[a.id for a in apis.values()]]
    await turn(hass, "weather and a picture", agent,
               [(await tool_named(hass, agent, "get_weather_forecast"), {"range": "today"}),
                (await tool_named(hass, agent, "search_images"), {"query": "rain"})])
    # The chat log runs a turn's tools side by side: each result is published, in the order they finish.
    assert sorted(s.state for s in seen) == ["Voice Satellite: Image Search", "Voice Satellite: Weather Forecast"]
    assert seen[0].attributes["time"] < seen[1].attributes["time"]


async def test_follow_up_turn(hass, apis):
    """A second turn in the same conversation carries its own words."""
    conversation = await turn(hass, "What does a pangolin look like?", ["voice_satellite_llm_tools_image_search"], [("search_images", {})])
    await turn(hass, "And the weather tomorrow?", ["voice_satellite_llm_tools_weather"], [("get_weather_forecast", {"range": "tomorrow"})], conversation)
    state = hass.states.get(ENTITY)
    assert (state.state, state.attributes["words"]) == ("Voice Satellite: Weather Forecast", "And the weather tomorrow?")


async def test_no_tools_no_update(hass, apis):
    seen = changes(hass)
    await turn(hass, "hello", ["assist"], [])
    assert seen == []


async def test_words_and_time_not_recorded():
    assert ToolCallSensor._unrecorded_attributes == frozenset({"words", "time"})


def test_display_rules():
    """Kiosk Satellite's rules for its result panels."""
    assert has_display("search_videos", {"results": [{"video_id": "abc"}]})
    assert not has_display("search_images", {"results": []})
    assert not has_display("get_weather_forecast", {"forecast": None})
    assert not has_display("get_financial_data", {"query_type": "stock"})
    assert has_display("x_financial-data__get_financial_data", {"query_type": "stock"})
    assert not has_display("search_images", {"error": False, "results": []})
    assert has_display("search_images", {"error": False, "results": [{"image_url": "u"}]})
    assert not has_display("HassTurnOn", "not a dict")


async def test_one_entry_only(hass):
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert again["type"] == "abort" and again["reason"] == "single_instance_allowed"


async def test_same_millisecond(hass, apis, monkeypatch):
    """Two results in the same millisecond still get different times, a millisecond apart, so both are updates."""
    from datetime import datetime, UTC
    from homeassistant.util import dt as dt_util
    frozen = datetime(2026, 11, 17, 6, 49, 12, 542000, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: frozen)
    seen = changes(hass)
    agent = ["assist", *[a.id for a in apis.values()]]
    await turn(hass, "weather and a picture", agent,
               [(await tool_named(hass, agent, "get_weather_forecast"), {"range": "today"}),
                (await tool_named(hass, agent, "search_images"), {"query": "rain"})])
    await turn(hass, "weather and a picture", agent,
               [(await tool_named(hass, agent, "get_weather_forecast"), {"range": "today"})])
    # The chat log runs a turn's tools side by side, so they may finish in either order.
    assert sorted(s.attributes["time"] for s in seen) == [
        "2026-11-17T06:49:12.542+00:00", "2026-11-17T06:49:12.543+00:00", "2026-11-17T06:49:12.544+00:00"]
