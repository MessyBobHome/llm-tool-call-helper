<p align="center"><img src="custom_components/llm_tool_call_helper/brand/logo@2x.png" alt="LLM Tool Call Helper" width="420"></p>

# LLM Tool Call Helper

A small Home Assistant integration with one sensor, `sensor.llm_tool_call`, that says which tool an AI conversation agent just used, the moment the tool returns, a few seconds before the agent has finished its answer.

> **🤖 AI disclosure:** This integration was developed with substantial help from AI (Anthropic's Claude), which wrote most of the code, documentation and artwork under my direction. Please review it before installing, and report any issues you find.

Home Assistant adds every tool call and result to a conversation's log as it happens, but nothing outside a conversation can see them while it's still running. This integration watches those logs inside Home Assistant and turns each tool result into a sensor update that automations, dashboards and displays can follow. It was made so a voice-assistant display can get ready for a picture or a weather card before it arrives, but it works for anything that wants to react to the tools your AI uses.

## The sensor

| | Example | What it is | In history |
| --- | --- | --- | --- |
| State | `Voice Satellite: Image Search` | The name of the LLM API the tool belongs to, as your conversation agent's settings list it. Home Assistant's own tools show as its built-in API (`Assist`, or `Device control` from Home Assistant 2026.11). | Yes |
| `display` | `true` | Whether the result has something a screen would show: pictures, videos, a weather forecast or a stock quote. It follows the rules Kiosk Satellite uses for its result panels, so a failed search or a Wikipedia article without a picture is `false`. | Yes |
| `words` | `What does a pangolin look like?` | What was said to start that turn, so a speaker can tell its own turn from another's. | No |
| `time` | `2026-11-11T18:42:07.315+00:00` | When the result came back, always at least a millisecond after the previous result's, so every result is an update, even the same tool with the same result twice. | No |

- It updates once for each tool result, not when the tool is called, and not for turns that use no tools.
- It works with any LLM API, from any integration, including ones installed later. Nothing is hard-coded: it asks Home Assistant which API each tool came from.
- There is one sensor for the whole house. Every conversation updates it: each voice satellite, the Assist dialog, and automations that talk to the AI. Home Assistant's conversation log doesn't record which device a conversation came from, so `words` is how a device tells its own turns apart.

## Requirements

- Home Assistant 2026.10 or newer. Tested on 2026.10 and 2026.11.
- An AI conversation agent with LLM APIs (tools) turned on, such as OpenAI, Google Gemini, Anthropic or Ollama.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MessyBobHome&repository=llm-tool-call-helper&category=integration)

1. Use the button above, or in HACS open **⋮ > Custom repositories**, add `https://github.com/MessyBobHome/llm-tool-call-helper` with the type **Integration**, and open it.
2. Choose **Download**, then restart Home Assistant.

### Manual

1. Download the latest release and copy `custom_components/llm_tool_call_helper` into your Home Assistant `config/custom_components` folder. Create `custom_components` next to `configuration.yaml` if it isn't there yet.
2. Restart Home Assistant.

### Set it up

[![Open your Home Assistant instance and start setting up LLM Tool Call Helper.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=llm_tool_call_helper)

Use the button, or go to **Settings > Devices & services > Add integration** and choose **LLM Tool Call Helper**. There is nothing to configure. It can only be added once, and it creates `sensor.llm_tool_call`.

To check it works, open **Developer tools > States**, find `sensor.llm_tool_call`, and ask your assistant something that uses a tool, such as "What does a pangolin look like?". The state changes to the tool's API as soon as the tool returns.

## Using it

### In automations

A state trigger with no `to` fires on every update, including the same tool twice, because `time` always changes. Check the state in a condition:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.llm_tool_call
conditions:
  - condition: template
    value_template: >
      {{ trigger.to_state.state == 'Voice Satellite: Image Search'
         and trigger.to_state.attributes.display }}
actions:
  - action: light.turn_on
    target:
      entity_id: light.living_room_tv_backlight
```

### With a display

A display that knows the words it just heard can follow the sensor and act on its own turns only: take an update when `words` matches what it heard, `time` is newer than the last update it used and recent, and `display` is `true`. The LCARS voice skin for Kiosk Satellite does exactly this to draw the frame for a picture or weather card before the answer is finished.

## Privacy

Everything stays inside Home Assistant. The integration has no network code: it follows the conversation logs in memory, through Home Assistant's own conversation component, and only writes its one sensor. It doesn't store the logs or anything from them.

The sensor's `words` attribute holds what was said to start the latest turn. It's left out of Home Assistant's history, along with `time`, so only the API name and `display` are recorded. To record nothing at all, exclude the sensor from the recorder:

```yaml
recorder:
  exclude:
    entities:
      - sensor.llm_tool_call
```

Anyone who can see your entities can see the sensor's current `words`, the same as any other entity.

## Performance

It does a few dictionary lookups for each entry Home Assistant adds to a conversation log, and writes the sensor once per tool result. The first time it sees a tool on an agent with several LLM APIs, it asks each API for its tool list to find the right name (a few milliseconds), and remembers the answer.

## Limitations

- **One sensor for the whole house:** if two devices' tools return within the same moment, a device following the sensor may miss its update. See [The sensor](#the-sensor).
- **Home Assistant internals:** it follows conversation logs through a hook inside Home Assistant's conversation component that isn't a published API for integrations. A future Home Assistant release could change it. If that happens, the sensor simply stops updating until a new version of this integration adapts.

## Development

The tests run in a real Home Assistant with [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component). Conversations go through Home Assistant's chat log, with LLM APIs whose tools return what common tools return.

```sh
pip install -r requirements_test.txt
pytest
```

The artwork sources are in `art/`. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Credits and license

The `display` rules follow how [Kiosk Satellite](https://kiosksatellite.com) decides which tool results get a panel. This project isn't affiliated with Home Assistant, the Open Home Foundation, Kiosk Satellite or any AI provider.

The logo's lettering is set in [Inter](https://rsms.me/inter/) (SIL Open Font License).

[Apache-2.0](LICENSE).
