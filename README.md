# tuiradio

A terminal UI internet radio player powered by the [Radio Browser](https://www.radio-browser.info/) community database.

![Python 3](https://img.shields.io/badge/python-3.10+-blue) [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

## Requirements

- Python 3.10+
- [mpv](https://mpv.io/) (the actual audio player)

## Installation

```bash
git clone <repo>
cd tuiradio
./run.sh        # creates .venv, installs dependencies, and launches
```

`run.sh` is self-bootstrapping — it creates a virtual environment and installs dependencies on the first run.

## Usage

On launch, the top 100 most-voted stations are loaded. Use the keyboard to navigate and play.

### Keybindings

| Key | Action |
|-----|--------|
| `↑` / `↓` | Navigate station list |
| `Enter` | Play selected station |
| `Ctrl+L` | Focus search bar |
| `r` | Reload top stations |
| `s` | Stop playback |
| `+` / `=` | Volume up |
| `-` | Volume down |
| `q` | Quit |

### Search

Press `Ctrl+L` to focus the search bar and press `Enter` to search. Plain text searches by station name. You can also use field prefixes to build more specific queries:

| Prefix | Example | Description |
|--------|---------|-------------|
| `tag:` | `tag:jazz` | Station has this tag |
| `country:` | `country:DE` | Country code (ISO 3166-1 alpha-2) |
| `codec:` | `codec:mp3` | Audio codec |
| `language:` | `language:french` | Spoken language |
| `bitrate:` | `bitrate:128` or `bitrate:128-320` | Bitrate floor, or range in kbps |
| `NOT tag:` | `NOT tag:pop` | Exclude stations with this tag |

Prefixes can be combined freely:

```
tag:rock country:DE NOT tag:metal
tag:jazz AND tag:blues bitrate:128-320
```

`AND` is optional and ignored — all terms are implicitly combined. Values cannot contain spaces.

Clearing the search bar and pressing `Enter` returns to the top stations list.

## Configuration

Settings are saved automatically to `~/.config/tuiradio/config.json`:

- Volume level
- Last played station
- Last search query (restored on next launch)

## Dependencies

| Library | License |
|---------|---------|
| [Textual](https://github.com/Textualize/textual) | MIT |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause |

## License

Copyright (C) 2026 surrogard — licensed under the [GNU General Public License v3.0](LICENSE).

## Development

This project was developed with the assistance of [Claude](https://claude.ai) (Anthropic).
