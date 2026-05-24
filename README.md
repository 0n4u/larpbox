<div align="center">

# WELCOME TO LARPBOX

**Open source VRChat tool**

<br>

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![PyQt6](https://img.shields.io/badge/PyQt6-6.5+-41CD52?style=for-the-badge&logo=qt&logoColor=white)](https://www.riverbankcomputing.com/software/pyqt/)
[![VRChat](https://img.shields.io/badge/VRChat-OSC%20%26%20API-000000?style=for-the-badge&logo=steam&logoColor=white)](https://hello.vrchat.com/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![Open Source](https://img.shields.io/badge/Open%20Source-Yes-8A2BE2?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](#open-source)
[![Contributions Welcome](https://img.shields.io/badge/Contributions-Welcome-22C55E?style=for-the-badge&logo=githubactions&logoColor=white)](#contributing)
[![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)

<br>

[Features](#features) · [Quick Start](#quick-start) · [Configuration](#configuration) · [Open Source](#open-source) · [Contributing](#contributing)

</div>

## What is larpbox?

**larpbox** is a free open source desktop app for VRChat. It runs alongside VRChat while you do your THANG.

**Chatbox OSC:** animated text presets, idle keep alives, and now playing media.

**Live preview:** see how your chatbox looks before it goes in game.

**VRChat integration:** friends list, instance player list, account panel, and avatar search.

**Social tools:** join friends, force clone avatars, moderate players, and more.

More features coming soon. Join our Discord and request stuff you want added or changed.

## Features

### Chatbox & OSC

**Animated presets** · Frame by frame chatbox animations from `presets.json`

**Live preview** · Real time layout preview with accurate wrapping

**OSC control** · Primary and optional secondary OSC endpoints (if you're a weirdo)

**Idle keep alive** · Blank/idle messages so your chatbox stays active

**Media overlay** · Windows now playing stacked on top of presets

**Stacking modes** · Egg mode, extreme height, and wall of china stacking for max aura

**Preset manager** · Built in editor to create, edit, and delete presets

### VRChat account integration

**Secure login** · VRChat auth with Remember Me and 2FA

**Friends panel** · Online status, trust ranks, world info, join and force clone from the context menu

**Player list** · Log based player detection plus API enrichment with trust badges

**Account panel** · Profile, bio, status, instance, and badges

**Avatar search** · AvtrDB, official API, and community endpoints with filters

### User tools

**Force clone** · Wear someone's avatar when cloning is disabled. Uses log and API resolution. Does not work a lot of the time. If you have workarounds please let me know.

**Join player** · Launch VRChat straight into a friend's instance

**Moderation** · Block, mute, or hide avatars from the player list

**Instance control** · Force close instances when you're the owner

**Avatar cache** · Avatar ID cache synced from VRChat logs

### UI

**Dark theme** · Frameless window, dark UI

**Smooth animations** · Panel transitions and staggered list loading

**Modular panels** · Toggle friends, search, player list, account, preview, and presets in settings

**Debug mode** · Optional console and file logging

## Quick Start

**Requirements**

Windows 10/11 (Linux support probably coming soon)

Python 3.11+

VRChat with OSC enabled (default port `9000`)

**Install**

```bash
pip install -r requirements.txt
```

**Run**

```bash
python run_app.py
```

Sign in on first launch. Turn on **Remember me** if you don't want to log in every time.

**Factory reset**

Wipes saved login, settings, avatar cache, and logs. Your presets stay. Delete `presets.json` yourself if you want those gone too.

```bash
python reset_app.py -y
```

## Configuration

Settings live in `config.json`. Copy `config.json.example` if you need a fresh start.

| Setting | Description |
|---------|-------------|
| `osc_ip` / `osc_port` | VRChat OSC endpoint (default `127.0.0.1:9000`) |
| `egg_mode` | Compact chatbox stacking with invisible characters |
| `enable_media` | Show now playing media from Windows |
| `message_interval` | Chatbox message timing in ms |
| `show_*` panel toggles | Show or hide UI panels |
| `avatar_search_provider` | Default avatar search backend |
| `debug_mode` | Console window and verbose logging |

Open **Settings** from the title bar. Most options save on their own.

## Development

```bash
python reset_app.py -y --debug
python run_app.py
```

## Open Source

The full codebase is here to read, fork, and mess with.

No telemetry. No ads. Python and PyQt6. Plain JSON config. Your auth tokens and cache stay on your machine.

```
config.json   local settings and session
presets.json  your chatbox animations
data/         avatar cache
logs/         debug logs
```

Nothing goes to third party servers except VRChat and avatar search API calls you actually trigger.

## Contributing

Bug fixes, new search providers, UI polish, docs, all welcome. Keep PRs focused and say what you changed.

## Disclaimer

larpbox is a third party tool. Not affiliated with or endorsed by VRChat Inc.

Use moderation, force clone, and instance tools responsibly. Read [VRChat's Terms of Service](https://vrchat.com/legal) and Community Guidelines.

<div align="center">

**Built for the VRChat community**

If you're larping with larpbox, star the repo

</div>
