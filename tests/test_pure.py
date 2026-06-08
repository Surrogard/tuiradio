"""Tests for pure / near-pure functions in tuiradio.py."""
import pytest

from tuiradio import _apply_filters, _parse_query


# ── helpers ───────────────────────────────────────────────────────────────────

def _station(**kwargs):
    defaults = {
        "name": "Test FM",
        "tags": "",
        "countrycode": "US",
        "codec": "mp3",
        "language": "english",
        "bitrate": 128,
        "votes": 10,
        "clickcount": 5,
        "stationuuid": "abc-123",
        "url_resolved": "http://stream.example.com/radio",
    }
    return {**defaults, **kwargs}


def _stub_app(song_title="", buffer_secs=None, theme_variables=None):
    """Minimal object that satisfies _render_buffer and _current_status_msg."""
    from tuiradio import TuiRadio

    class _AppStub:
        _render_buffer = TuiRadio._render_buffer
        _buffer_colors = TuiRadio._buffer_colors
        _current_status_msg = TuiRadio._current_status_msg

        def __init__(self, song_title="", buffer_secs=None, theme_variables=None):
            self._song_title = song_title
            self._buffer_secs = buffer_secs
            self.theme_variables = theme_variables or {}

    return _AppStub(song_title=song_title, buffer_secs=buffer_secs, theme_variables=theme_variables)


# ── _parse_query ──────────────────────────────────────────────────────────────

class TestParseQueryPlainName:
    def test_plain_text_becomes_name_param(self):
        p = _parse_query("jazz radio")
        assert p.api_params["name"] == "jazz radio"

    def test_empty_query_produces_no_params(self):
        p = _parse_query("")
        assert p.api_params == {}
        assert p.exclude_tags == []
        assert p.exclude_fields == []

    def test_and_keyword_stripped_from_name(self):
        p = _parse_query("jazz AND blues")
        assert "AND" not in p.api_params.get("name", "")

    def test_whitespace_only_produces_no_params(self):
        p = _parse_query("   ")
        assert p.api_params == {}


class TestParseQueryTag:
    def test_single_tag(self):
        p = _parse_query("tag:jazz")
        assert p.api_params["tagList"] == "jazz"

    def test_multiple_tags_joined(self):
        p = _parse_query("tag:jazz tag:blues")
        assert set(p.api_params["tagList"].split(",")) == {"jazz", "blues"}

    def test_duplicate_tags_deduplicated(self):
        p = _parse_query("tag:jazz tag:jazz")
        assert p.api_params["tagList"].count("jazz") == 1

    def test_not_tag_goes_to_exclude(self):
        p = _parse_query("NOT tag:pop")
        assert "pop" in p.exclude_tags
        assert "tagList" not in p.api_params

    def test_tags_alias(self):
        p = _parse_query("tags:rock")
        assert p.api_params["tagList"] == "rock"

    def test_tag_case_insensitive(self):
        p = _parse_query("tag:Jazz")
        assert p.api_params["tagList"] == "jazz"

    def test_not_tag_case_insensitive(self):
        p = _parse_query("NOT tag:Pop")
        assert "pop" in p.exclude_tags


class TestParseQueryCountry:
    def test_country_uppercased(self):
        p = _parse_query("country:de")
        assert p.api_params["countrycode"] == "DE"

    def test_not_country_goes_to_exclude(self):
        p = _parse_query("NOT country:US")
        assert ("countrycode", "us") in p.exclude_fields


class TestParseQueryCodec:
    def test_codec_param(self):
        p = _parse_query("codec:aac")
        assert p.api_params["codec"] == "aac"

    def test_not_codec_goes_to_exclude(self):
        p = _parse_query("NOT codec:aac")
        assert ("codec", "aac") in p.exclude_fields


class TestParseQueryLanguage:
    def test_language_param(self):
        p = _parse_query("language:french")
        assert p.api_params["language"] == "french"

    def test_not_language_goes_to_exclude(self):
        p = _parse_query("NOT language:german")
        assert ("language", "german") in p.exclude_fields


class TestParseQueryBitrate:
    def test_single_bitrate_sets_min(self):
        p = _parse_query("bitrate:128")
        assert p.api_params["bitrateMin"] == 128
        assert "bitrateMax" not in p.api_params

    def test_bitrate_range(self):
        p = _parse_query("bitrate:128-320")
        assert p.api_params["bitrateMin"] == 128
        assert p.api_params["bitrateMax"] == 320

    def test_bitrate_range_no_lower(self):
        p = _parse_query("bitrate:-320")
        assert "bitrateMin" not in p.api_params
        assert p.api_params["bitrateMax"] == 320

    def test_invalid_bitrate_ignored(self):
        p = _parse_query("bitrate:abc")
        assert "bitrateMin" not in p.api_params

    def test_not_bitrate_ignored(self):
        p = _parse_query("NOT bitrate:128")
        assert "bitrateMin" not in p.api_params


class TestParseQueryCombined:
    def test_tag_and_country_combined(self):
        p = _parse_query("tag:rock country:DE")
        assert p.api_params["tagList"] == "rock"
        assert p.api_params["countrycode"] == "DE"
        assert "name" not in p.api_params

    def test_name_with_filters(self):
        p = _parse_query("smooth jazz tag:jazz country:US")
        assert p.api_params["name"] == "smooth jazz"
        assert p.api_params["tagList"] == "jazz"
        assert p.api_params["countrycode"] == "US"

    def test_unknown_prefix_left_in_name(self):
        p = _parse_query("foo:bar baz")
        assert "baz" in p.api_params.get("name", "")


# ── _apply_filters ────────────────────────────────────────────────────────────

class TestApplyFilters:
    def test_no_filters_returns_all(self):
        stations = [_station(tags="rock"), _station(tags="pop")]
        p = _parse_query("")
        assert _apply_filters(stations, p) == stations

    def test_exclude_tag_removes_matching(self):
        keep = _station(tags="rock")
        drop = _station(tags="pop")
        p = _parse_query("NOT tag:pop")
        result = _apply_filters([keep, drop], p)
        assert result == [keep]

    def test_exclude_tag_partial_match_not_excluded(self):
        # "pop" should not exclude a station tagged "pop-punk" — tags are comma-separated items
        station = _station(tags="pop-punk,rock")
        p = _parse_query("NOT tag:pop")
        result = _apply_filters([station], p)
        assert result == [station]

    def test_exclude_tag_case_insensitive(self):
        drop = _station(tags="Pop")
        p = _parse_query("NOT tag:pop")
        result = _apply_filters([drop], p)
        assert result == []

    def test_exclude_tag_comma_separated(self):
        drop = _station(tags="jazz, pop, blues")
        p = _parse_query("NOT tag:pop")
        result = _apply_filters([drop], p)
        assert result == []

    def test_exclude_country(self):
        keep = _station(countrycode="DE")
        drop = _station(countrycode="US")
        p = _parse_query("NOT country:US")
        result = _apply_filters([keep, drop], p)
        assert result == [keep]

    def test_exclude_codec(self):
        keep = _station(codec="aac")
        drop = _station(codec="aac")
        p = _parse_query("NOT codec:aac")
        result = _apply_filters([keep, drop], p)
        assert result == []

    def test_multiple_excludes_all_applied(self):
        drop_tag = _station(tags="pop", countrycode="DE")
        drop_country = _station(tags="rock", countrycode="US")
        keep = _station(tags="rock", countrycode="DE")
        p = _parse_query("NOT tag:pop NOT country:US")
        result = _apply_filters([drop_tag, drop_country, keep], p)
        assert result == [keep]

    def test_empty_tags_field_not_excluded(self):
        station = _station(tags="")
        p = _parse_query("NOT tag:pop")
        assert _apply_filters([station], p) == [station]

    def test_none_tags_field_not_excluded(self):
        station = _station(tags=None)
        p = _parse_query("NOT tag:pop")
        assert _apply_filters([station], p) == [station]


# ── _render_buffer ────────────────────────────────────────────────────────────

class TestRenderBuffer:
    def test_none_returns_empty(self):
        app = _stub_app(buffer_secs=None)
        assert app._render_buffer() == ""

    def test_zero_seconds_red_empty_bar(self):
        app = _stub_app(buffer_secs=0.0)
        result = app._render_buffer()
        assert "red" in result
        assert "░" * 10 in result

    def test_below_one_second_red(self):
        app = _stub_app(buffer_secs=0.5)
        assert "red" in app._render_buffer()

    def test_one_to_two_seconds_yellow(self):
        app = _stub_app(buffer_secs=1.5)
        assert "yellow" in app._render_buffer()

    def test_two_or_more_seconds_green(self):
        app = _stub_app(buffer_secs=2.0)
        assert "green" in app._render_buffer()

    def test_ten_seconds_full_bar(self):
        app = _stub_app(buffer_secs=10.0)
        result = app._render_buffer()
        assert "█" * 10 in result
        assert "░" not in result

    def test_beyond_ten_capped_at_full(self):
        app = _stub_app(buffer_secs=30.0)
        result = app._render_buffer()
        assert "█" * 10 in result

    def test_label_shows_seconds(self):
        app = _stub_app(buffer_secs=3.7)
        assert "3.7s" in app._render_buffer()

    def test_partial_fill(self):
        app = _stub_app(buffer_secs=5.0)
        result = app._render_buffer()
        assert "█" * 5 in result
        assert "░" * 5 in result


# ── _current_status_msg ───────────────────────────────────────────────────────

class TestCurrentStatusMsg:
    def test_no_title_returns_playing(self):
        app = _stub_app(song_title="")
        assert app._current_status_msg() == "Playing…"

    def test_title_prefixed_with_note(self):
        app = _stub_app(song_title="Miles Davis - So What")
        assert app._current_status_msg() == "♪  Miles Davis - So What"

    def test_whitespace_title_treated_as_empty(self):
        # _update_song_title stores `msg.get("data") or ""` so empty string
        # and None both become "" — but a whitespace-only string would pass through
        app = _stub_app(song_title="  ")
        assert app._current_status_msg().startswith("♪")


# ── theme config ─────────────────────────────────────────────────────────────

class TestThemeConfig:
    def test_default_theme_when_no_config(self, tmp_path, monkeypatch):
        import tuiradio
        monkeypatch.setattr(tuiradio, "CONFIG_PATH", tmp_path / "config.json")
        from tuiradio import TuiRadio
        app = TuiRadio.__new__(TuiRadio)
        app._volume = 100
        app._last_station_uuid = ""
        app._last_search = ""
        app._staged_theme = "textual-dark"
        app._load_config()
        assert app._staged_theme == "textual-dark"

    def test_theme_loaded_from_config(self, tmp_path, monkeypatch):
        import tuiradio, json
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"volume": 80, "last_station_uuid": "", "last_search": "", "theme": "amber"}))
        monkeypatch.setattr(tuiradio, "CONFIG_PATH", cfg)
        from tuiradio import TuiRadio
        app = TuiRadio.__new__(TuiRadio)
        app._volume = 100
        app._last_station_uuid = ""
        app._last_search = ""
        app._staged_theme = "textual-dark"
        app._load_config()
        assert app._staged_theme == "amber"

    def test_invalid_theme_preserves_prior_value(self, tmp_path, monkeypatch):
        import tuiradio, json
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"theme": "nonexistent-theme"}))
        monkeypatch.setattr(tuiradio, "CONFIG_PATH", cfg)
        from tuiradio import TuiRadio
        app = TuiRadio.__new__(TuiRadio)
        app._volume = 100
        app._last_station_uuid = ""
        app._last_search = ""
        app._staged_theme = "nord"
        app._load_config()
        assert app._staged_theme == "nord"

    def test_theme_saved_to_config(self, tmp_path, monkeypatch):
        import tuiradio, json
        monkeypatch.setattr(tuiradio, "CONFIG_PATH", tmp_path / "config.json")
        from tuiradio import TuiRadio
        app = TuiRadio.__new__(TuiRadio)
        app._volume = 80
        app._last_station_uuid = "abc"
        app._last_search = "jazz"
        app._staged_theme = "green"
        app._reactive_theme = "green"
        app._save_config()
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["theme"] == "green"

    def test_alias_theme_saved_as_alias_name(self, tmp_path, monkeypatch):
        import tuiradio, json
        monkeypatch.setattr(tuiradio, "CONFIG_PATH", tmp_path / "config.json")
        from tuiradio import TuiRadio
        app = TuiRadio.__new__(TuiRadio)
        app._volume = 100
        app._last_station_uuid = ""
        app._last_search = ""
        app._staged_theme = "textual-dark"
        app._reactive_theme = "textual-dark"
        app._save_config()
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["theme"] == "default"


# ── buffer bar colors ─────────────────────────────────────────────────────────

class TestBufferColors:
    def test_uses_theme_success_for_full_buffer(self):
        app = _stub_app(buffer_secs=5.0, theme_variables={"success": "#00ff00", "warning": "#ffff00", "error": "#ff0000"})
        result = app._render_buffer()
        assert "#00ff00" in result

    def test_uses_theme_warning_for_low_buffer(self):
        app = _stub_app(buffer_secs=1.5, theme_variables={"success": "#00ff00", "warning": "#ffff00", "error": "#ff0000"})
        result = app._render_buffer()
        assert "#ffff00" in result

    def test_uses_theme_error_for_critical_buffer(self):
        app = _stub_app(buffer_secs=0.5, theme_variables={"success": "#00ff00", "warning": "#ffff00", "error": "#ff0000"})
        result = app._render_buffer()
        assert "#ff0000" in result

    def test_falls_back_to_named_colors_when_no_theme_variables(self):
        app = _stub_app(buffer_secs=5.0, theme_variables={})
        result = app._render_buffer()
        assert "green" in result

    def test_empty_when_no_buffer_secs(self):
        app = _stub_app(buffer_secs=None)
        result = app._render_buffer()
        assert result == ""

