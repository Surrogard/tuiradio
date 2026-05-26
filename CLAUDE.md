# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
./run             # auto-creates .venv and installs deps on first run, then launches
```

Direct execution (after `.venv` exists):
```bash
.venv/bin/python tuiradio.py
```

## External Dependency

`mpv` must be installed on the system — it is the actual audio player. The app launches it as a subprocess and communicates via Unix socket IPC at `/tmp/tuiradio-<pid>.sock`. Without `mpv`, playback silently fails with an error message in the status bar.

## Architecture

The entire application lives in a single file: `tuiradio.py`. It is a [Textual](https://textual.textualize.io/) `App` subclass (`TuiRadio`) with no external modules.

**Data flow:**
1. On mount, `_fetch_top()` or `_fetch_search()` (both `@work(thread=True)` background workers) call the [Radio Browser API](https://de1.api.radio-browser.info/) and hand results back to the main thread via `call_from_thread`.
2. `_populate()` fills the `DataTable` with station rows; `self._stations` is the authoritative list backing the table — row index == list index.
3. Selecting a row calls `_play()`, which spawns `mpv` as a subprocess.
4. `_track_song_title()` (another background worker) opens a persistent Unix socket to mpv, subscribes to `media-title` property changes, and pushes updates back via `call_from_thread(_update_song_title)`.
5. Volume changes use `_mpv_cmd()` to send one-shot JSON IPC commands to the same socket.

**Config persistence** (`~/.config/tuiradio/config.json`): volume, last station UUID, and last search query are loaded in `__init__` and saved in `action_quit`.

**Key bindings** are declared in `BINDINGS` and each maps to an `action_*` method. The `+`/`=` keys both trigger `action_volume_up` (one is shown in footer, the other is `show=False`).
