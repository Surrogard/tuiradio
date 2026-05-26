#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 surrogard
"""tuiradio – A TUI internet radio player backed by the Radio Browser API."""

import json
import os
import pathlib
import re
import socket
import subprocess
from typing import NamedTuple, Optional

import httpx
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static
from textual import on, work
from textual.timer import Timer

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

RADIO_API = "https://de1.api.radio-browser.info/json"
_HEADERS = {"User-Agent": "tuiradio/1.0"}
CONFIG_PATH = pathlib.Path.home() / ".config" / "tuiradio" / "config.json"


# ── search query parser ──────────────────────────────────────────────────────

class _Parsed(NamedTuple):
    api_params: dict
    exclude_tags: list    # lowercased tag values for NOT tag:
    exclude_fields: list  # list of (station_key, value) for NOT country:/codec:/language:


_TOKEN_RE = re.compile(r'(NOT\s+)?(\w+):(\S+)', re.IGNORECASE)

_PREFIX_MAP = {
    "tag": "tag", "tags": "tag",
    "country": "countrycode",
    "codec": "codec",
    "language": "language",
    "bitrate": "bitrate",
}


def _parse_query(query: str) -> _Parsed:
    api_params: dict = {}
    exclude_tags: list = []
    exclude_fields: list = []
    positive_tags: list = []
    remainder = query

    for m in _TOKEN_RE.finditer(query):
        is_not = bool(m.group(1))
        prefix = m.group(2).lower()
        value  = m.group(3)
        canon  = _PREFIX_MAP.get(prefix)

        if canon is None:
            continue  # unknown prefix — leave in remainder for name=

        remainder = remainder.replace(m.group(0), "", 1)

        if canon == "tag":
            if is_not:
                exclude_tags.append(value.lower())
            else:
                positive_tags.append(value.lower())
        elif canon == "bitrate":
            if not is_not:
                parts = value.split("-")
                try:
                    if parts[0]:
                        api_params["bitrateMin"] = int(parts[0])
                    if len(parts) == 2 and parts[1]:
                        api_params["bitrateMax"] = int(parts[1])
                except ValueError:
                    pass
        elif canon == "countrycode":
            if is_not:
                exclude_fields.append(("countrycode", value.lower()))
            else:
                api_params["countrycode"] = value.upper()
        else:  # codec, language
            if is_not:
                exclude_fields.append((canon, value.lower()))
            else:
                api_params[canon] = value

    if positive_tags:
        api_params["tagList"] = ",".join(dict.fromkeys(positive_tags))

    name = re.sub(r'\bAND\b', '', remainder, flags=re.IGNORECASE).strip()
    if name:
        api_params["name"] = name

    return _Parsed(api_params, exclude_tags, exclude_fields)


def _apply_local_query(stations: list[dict], q: str) -> list[dict]:
    """Filter an in-memory station list against query q without hitting the API."""
    if not q:
        return stations
    parsed = _parse_query(q)
    result = _apply_filters(stations, parsed)
    name = parsed.api_params.get("name", "").lower()
    if name:
        result = [s for s in result if name in (s.get("name") or "").lower()]
    return result


def _apply_filters(stations: list[dict], parsed: _Parsed) -> list[dict]:
    if not parsed.exclude_tags and not parsed.exclude_fields:
        return stations
    result = []
    for s in stations:
        station_tags = {t.strip().lower() for t in (s.get("tags") or "").split(",")}
        if any(t in station_tags for t in parsed.exclude_tags):
            continue
        if any((s.get(key) or "").lower() == val for key, val in parsed.exclude_fields):
            continue
        result.append(s)
    return result


class TuiRadio(App):
    """Terminal UI internet radio player."""

    CSS = """
    Screen { layout: vertical; }

    #search-row {
        height: 3;
        padding: 0 1;
        background: $panel;
    }
    #search { width: 1fr; }
    #spinner {
        width: 2;
        content-align: center middle;
        color: $accent;
    }

    #stations { height: 1fr; }

    #status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    #status.playing { color: $success; }
    """

    BINDINGS = [
        Binding("ctrl+l", "focus_search", "Search"),
        Binding("+", "volume_up", "Vol +"),
        Binding("=", "volume_up", "Vol +", show=False),
        Binding("-", "volume_down", "Vol -"),
        Binding("s", "stop", "Stop"),
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Top stations"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._player: Optional[subprocess.Popen] = None
        self._stations: list[dict] = []
        self._current: Optional[dict] = None
        self._volume: int = 100
        self._ipc_path: str = f"/tmp/tuiradio-{os.getpid()}.sock"
        self._watching: bool = False
        self._song_title: str = ""
        self._buffer_secs: Optional[float] = None
        self._last_station_uuid: str = ""
        self._last_search: str = ""
        self._debounce_timer: Optional[Timer] = None
        self._all_stations: list[dict] = []
        self._api_busy: int = 0
        self._spinner_frame: int = 0
        self._spinner_timer: Optional[Timer] = None
        self._load_config()

    # ── config persistence ───────────────────────────────────────────────

    def _load_config(self) -> None:
        try:
            data = json.loads(CONFIG_PATH.read_text())
            self._volume = int(data.get("volume", 100))
            self._last_station_uuid = data.get("last_station_uuid", "")
            self._last_search = data.get("last_search", "")
        except Exception:
            pass

    def _save_config(self) -> None:
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(
                json.dumps({
                    "volume": self._volume,
                    "last_station_uuid": self._last_station_uuid,
                    "last_search": self._last_search,
                }, indent=2)
            )
        except Exception:
            pass

    # ── layout ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="search-row"):
            yield Static("", id="spinner")
            yield Input(
                placeholder="Search: name  or  tag:rock country:DE codec:mp3 NOT tag:pop  (Ctrl+L to focus)",
                id="search",
            )
        yield DataTable(id="stations", cursor_type="row")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_columns()
        search_input = self.query_one("#search", Input)
        if self._last_search:
            search_input.value = self._last_search
            self._fetch_search(self._last_search)
        else:
            self._fetch_top()

    def on_resize(self) -> None:
        self._rebuild_columns()
        if self._stations:
            self._populate(self._stations)

    # ── column helpers ───────────────────────────────────────────────────

    def _name_col_width(self) -> int:
        # Fixed cols: Country(7) + Tags(20) + Bitrate(8) + Codec(5)
        #           + Votes(7) + Clicks(7) = 54
        # ~12 chars for DataTable cell padding and borders across 7 columns
        return max(20, self.size.width - 54 - 16)

    def _rebuild_columns(self) -> None:
        t = self.query_one("#stations", DataTable)
        t.clear(columns=True)
        t.add_column("Name", width=self._name_col_width())
        t.add_column("Country", width=7)
        t.add_column("Tags", width=20)
        t.add_column("Bitrate", width=8)
        t.add_column("Codec", width=5)
        t.add_column("Votes", width=7)
        t.add_column("Clicks", width=7)

    # ── workers (run in background threads) ─────────────────────────────

    def _api_start(self) -> None:
        self._api_busy += 1
        if self._spinner_timer is None:
            self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _api_done(self) -> None:
        self._api_busy = max(0, self._api_busy - 1)
        if self._api_busy == 0 and self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
            self.query_one("#spinner", Static).update("")

    def _tick_spinner(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        self.query_one("#spinner", Static).update(_SPINNER_FRAMES[self._spinner_frame])

    @work(exclusive=True, thread=True)
    def _fetch_top(self) -> None:
        self.call_from_thread(self._api_start)
        self.call_from_thread(self._set_status, "Loading top stations…")
        try:
            with httpx.Client(timeout=10) as c:
                r = c.get(
                    f"{RADIO_API}/stations/topvote",
                    params={"limit": 100, "hidebroken": "true"},
                    headers=_HEADERS,
                )
                r.raise_for_status()
                self.call_from_thread(self._populate, r.json())
        except Exception as exc:
            self.call_from_thread(self._set_status, f"Error loading stations: {exc}")
        finally:
            self.call_from_thread(self._api_done)

    @work(exclusive=True, thread=True)
    def _fetch_search(self, query: str) -> None:
        self.call_from_thread(self._api_start)
        self.call_from_thread(self._set_status, f'Searching for "{query}"…')
        parsed = _parse_query(query)
        if not parsed.api_params:
            self.call_from_thread(self._api_done)
            self.call_from_thread(self._fetch_top)
            return
        try:
            with httpx.Client(timeout=10) as c:
                r = c.get(
                    f"{RADIO_API}/stations/search",
                    params={
                        **parsed.api_params,
                        "limit": 100,
                        "hidebroken": "true",
                        "order": "votes",
                        "reverse": "true",
                    },
                    headers=_HEADERS,
                )
                r.raise_for_status()
                stations = _apply_filters(r.json(), parsed)
                self.call_from_thread(self._populate, stations)
        except Exception as exc:
            self.call_from_thread(self._set_status, f"Search error: {exc}")
        finally:
            self.call_from_thread(self._api_done)

    # ── UI helpers ───────────────────────────────────────────────────────

    def _populate(self, data: list[dict], *, track_full: bool = True) -> None:
        if track_full:
            self._all_stations = data
        self._stations = data
        t = self.query_one("#stations", DataTable)
        t.clear()
        for s in data:
            bitrate = f"{s['bitrate']}k" if s.get("bitrate") else "—"
            tags = (s.get("tags") or "")[:20]
            name = (s.get("name") or "")[:self._name_col_width()]
            votes = str(s.get("votes") or 0)
            clicks = str(s.get("clickcount") or 0)
            t.add_row(
                name,
                s.get("countrycode", ""),
                tags,
                bitrate,
                s.get("codec", ""),
                votes,
                clicks,
            )
        self._set_status(f"{len(data)} stations  •  ↑↓ navigate  •  Enter to play")
        # Restore cursor to last listened station (full fetches only)
        if track_full and self._last_station_uuid:
            for idx, s in enumerate(data):
                if s.get("stationuuid") == self._last_station_uuid:
                    t.move_cursor(row=idx)
                    break

    def _set_status(self, msg: str) -> None:
        bar = self.query_one("#status", Static)
        if self._current:
            buf = self._render_buffer()
            buf_part = f"   │   {buf}" if buf else ""
            bar.update(f"▶  {self._current['name']}   │   vol: {self._volume}%   │   {msg}{buf_part}")
            bar.add_class("playing")
        else:
            bar.update(msg)
            bar.remove_class("playing")

    def _render_buffer(self) -> str:
        if self._buffer_secs is None:
            return ""
        secs = self._buffer_secs
        fill = round(min(secs, 10))
        bar = "█" * fill + "░" * (10 - fill)
        label = f"{secs:.1f}s"
        if secs >= 2.0:
            color = "green"
        elif secs >= 1.0:
            color = "yellow"
        else:
            color = "red"
        return f"buf [{color}]{bar} {label}[/{color}]"

    # ── playback ─────────────────────────────────────────────────────────

    @work(exclusive=False, thread=True)
    def _notify_click(self, uuid: str) -> None:
        """Tell Radio Browser a station was played (community courtesy)."""
        if not uuid:
            return
        try:
            with httpx.Client(timeout=5) as c:
                c.get(f"{RADIO_API}/url/{uuid}", headers=_HEADERS)
        except Exception:
            pass

    def _play(self, station: dict) -> None:
        self._stop_player()
        url = station.get("url_resolved") or station.get("url", "")
        if not url:
            self._set_status("No stream URL available for this station")
            return
        self._current = station
        self._last_station_uuid = station.get("stationuuid", "")
        self._song_title = ""
        self._buffer_secs = None
        self._notify_click(self._last_station_uuid)
        try:
            self._player = subprocess.Popen(
                [
                    "mpv", "--no-video", "--no-terminal",
                    f"--volume={self._volume}",
                    f"--input-ipc-server={self._ipc_path}",
                    "--",
                    url,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._watching = True
            self._track_song_title()
            self._set_status("Connecting…")
        except FileNotFoundError:
            self._set_status("mpv not found — install with: sudo pacman -S mpv")
        except Exception as exc:
            self._set_status(f"Playback error: {exc}")

    def _mpv_cmd(self, *args) -> None:
        """Send a JSON IPC command to the running mpv process."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(self._ipc_path)
                s.sendall(json.dumps({"command": list(args)}).encode() + b"\n")
        except Exception:
            pass

    @work(exclusive=False, thread=True)
    def _track_song_title(self) -> None:
        """Keep a persistent IPC connection and stream media-title changes."""
        import time
        # Wait up to 2 s for mpv to create the socket.
        for _ in range(20):
            if os.path.exists(self._ipc_path):
                break
            time.sleep(0.1)
        else:
            return
        my_player = self._player  # snapshot — exit if station changes under us
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(self._ipc_path)
                s.sendall(
                    json.dumps({"command": ["observe_property", 1, "media-title"]}).encode()
                    + b"\n"
                )
                s.sendall(
                    json.dumps({"command": ["observe_property", 2, "demuxer-cache-duration"]}).encode()
                    + b"\n"
                )
                buf = b""
                while self._watching and self._player is my_player:
                    try:
                        s.settimeout(1)
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            msg = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if (
                            msg.get("event") == "property-change"
                            and msg.get("name") == "media-title"
                        ):
                            title = msg.get("data") or ""
                            self.call_from_thread(self._update_song_title, title)
                        elif (
                            msg.get("event") == "property-change"
                            and msg.get("name") == "demuxer-cache-duration"
                        ):
                            data = msg.get("data")
                            if data is not None:
                                try:
                                    self.call_from_thread(self._update_buffer, float(data))
                                except (TypeError, ValueError):
                                    pass
        except Exception:
            pass

    def _current_status_msg(self) -> str:
        return f"♪  {self._song_title}" if self._song_title else "Playing…"

    def _update_song_title(self, title: str) -> None:
        self._song_title = title
        self._set_status(self._current_status_msg())

    def _update_buffer(self, seconds: float) -> None:
        self._buffer_secs = seconds
        self._set_status(self._current_status_msg())

    def _stop_player(self) -> None:
        self._watching = False
        if self._player:
            self._player.terminate()
            try:
                self._player.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._player.kill()
            self._player = None
        self._song_title = ""
        self._buffer_secs = None
        try:
            os.unlink(self._ipc_path)
        except FileNotFoundError:
            pass

    # ── actions & events ─────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_volume_up(self) -> None:
        self._volume = min(100, self._volume + 5)
        self._mpv_cmd("set_property", "volume", self._volume)
        if self._current:
            self._set_status(self._current_status_msg())

    def action_volume_down(self) -> None:
        self._volume = max(0, self._volume - 5)
        self._mpv_cmd("set_property", "volume", self._volume)
        if self._current:
            self._set_status(self._current_status_msg())

    def action_stop(self) -> None:
        self._stop_player()
        self._current = None
        self._set_status("Stopped")

    def action_reload(self) -> None:
        self.query_one("#search", Input).clear()
        self._fetch_top()

    def action_quit(self) -> None:
        self._stop_player()
        self._save_config()
        self.exit()

    @on(Input.Changed, "#search")
    def on_search_changed(self, event: Input.Changed) -> None:
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
        q = event.value.strip()
        self._last_search = q
        # instant local filter for immediate feedback
        if self._all_stations:
            self._populate(_apply_local_query(self._all_stations, q), track_full=False)
        # debounced API call for fresh / broader results
        if q:
            self._debounce_timer = self.set_timer(0.4, lambda: self._fetch_search(q))
        else:
            self._debounce_timer = self.set_timer(0.4, self._fetch_top)

    @on(Input.Submitted, "#search")
    def on_search_submit(self, event: Input.Submitted) -> None:
        if self._debounce_timer is not None:
            self._debounce_timer.stop()
        q = event.value.strip()
        self._last_search = q
        if q:
            self._fetch_search(q)
        else:
            self._fetch_top()
        self.query_one("#stations", DataTable).focus()

    @on(DataTable.RowSelected, "#stations")
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._stations):
            self._play(self._stations[idx])


# ── doctor mode ─────────────────────────────────────────────────────────────

def _doctor() -> None:
    """Run startup diagnostics and exit.  Invoked with --doctor."""
    import ssl
    import sys
    import shutil
    import importlib.metadata as meta

    OK   = "\033[32m✔\033[0m"
    FAIL = "\033[31m✘\033[0m"
    WARN = "\033[33m!\033[0m"
    DIM  = "\033[2m"
    RST  = "\033[0m"

    failed: set[str] = set()

    def check(label: str, fn, *hints: str) -> bool:
        try:
            print(f"  {OK}  {label}: {fn()}")
            return True
        except Exception as exc:
            print(f"  {FAIL}  {label}: {exc}")
            for h in hints:
                print(f"       {WARN}  {h}")
            failed.add(label)
            return False

    print("\ntuiradio doctor\n")

    # ── environment ──────────────────────────────────────────────────────────
    print("environment:")
    check("python", lambda: sys.version.split()[0])
    check("venv",   lambda: sys.prefix)
    for pkg in ("httpx", "textual", "certifi"):
        check(pkg, lambda p=pkg: meta.version(p),
              f"reinstall:  pip install --upgrade {pkg}")

    def _mpv():
        path = shutil.which("mpv")
        if not path:
            raise FileNotFoundError("not found")
        return path
    check("mpv", _mpv,
          "apt:    sudo apt install mpv",
          "pacman: sudo pacman -S mpv",
          "brew:   brew install mpv")

    # ── env vars ─────────────────────────────────────────────────────────────
    print("\nenv vars:")
    _ENV = [
        # (name, who-uses-it)
        ("SSL_CERT_FILE",   "Python ssl — override CA bundle path"),
        ("SSL_CERT_DIR",    "Python ssl — directory of CA certs"),
        ("HTTPS_PROXY",     "httpx — HTTPS proxy"),
        ("HTTP_PROXY",      "httpx — HTTP proxy"),
        ("ALL_PROXY",       "httpx — fallback proxy for all schemes"),
        ("NO_PROXY",        "httpx — comma-separated proxy bypass list"),
        ("HTTPX_LOG_LEVEL", "httpx — trace|debug|info|warning|error"),
    ]
    for var, desc in _ENV:
        val = os.environ.get(var) or os.environ.get(var.lower())
        if val:
            print(f"  {OK}  {var}={val!r}  {DIM}# {desc}{RST}")
        else:
            print(f"  {DIM}  -  {var:<16}  # {desc}{RST}")

    # ── TLS / CA bundle ──────────────────────────────────────────────────────
    print("\ntls:")

    def _ca_bundle():
        import certifi
        return certifi.where()
    check("ca bundle",   _ca_bundle,
          "pip install --upgrade certifi")
    check("ssl context", lambda: ssl.create_default_context().check_hostname and "ok")

    # ── connectivity ─────────────────────────────────────────────────────────
    print("\nconnectivity:")
    host = "de1.api.radio-browser.info"

    def _tcp():
        s = socket.create_connection((host, 443), timeout=5)
        s.close()
        return f"{host}:443 reachable"
    check("tcp", _tcp,
          "host unreachable — check network / firewall",
          f"probe manually:  nc -zv {host} 443")

    def _tls_verify():
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(
            socket.create_connection((host, 443), timeout=5),
            server_hostname=host,
        ) as tls:
            cert = tls.getpeercert()
            return cert.get("subject", ((('commonName', host),),))[0][0][1]

    tls_ok = check("tls (verified)", _tls_verify)

    if not tls_ok:
        # probe without verification to distinguish cert vs. network failure
        def _tls_noverify():
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
            with ctx.wrap_socket(
                socket.create_connection((host, 443), timeout=5),
                server_hostname=host,
            ) as tls:
                raw = tls.getpeercert(binary_form=True)
                return f"connected — {len(ssl.DER_cert_to_PEM_cert(raw))} byte cert"

        noverify_ok = check("tls (unverified)", _tls_noverify)
        print()
        if noverify_ok:
            print(f"  {WARN}  host reachable but cert verification failed — likely causes:")
            print( "          1. corporate / MITM proxy with its own CA")
            print( "             → export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt")
            print( "             → or point to your corporate bundle")
            print( "          2. stale certifi CA bundle")
            print( "             → pip install --upgrade certifi")
        else:
            print(f"  {WARN}  no TLS connection at all — firewall blocking 443?")
            print( "          → check HTTPS_PROXY / NO_PROXY if behind a proxy")
        print()

    def _httpx_get():
        r = httpx.get(f"https://{host}/json/stats", headers=_HEADERS, timeout=8)
        r.raise_for_status()
        return f"HTTP {r.status_code}"
    check("httpx GET", _httpx_get)

    print()
    sys.exit(0)


if __name__ == "__main__":
    import sys as _sys
    if "--doctor" in _sys.argv:
        _doctor()
    TuiRadio().run()

