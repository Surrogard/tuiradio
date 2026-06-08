"""Verify that mpv is invoked with -- before the URL to prevent option injection."""
# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name
# pylint: disable=unused-import,unused-argument,unsupported-membership-test,unsubscriptable-object
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tuiradio import TuiRadio


@pytest.fixture
def app():
    a = TuiRadio()
    a._ipc_path = "/tmp/tuiradio-test.sock"
    a._volume = 80
    return a


def _captured_args(app, url):
    """Run _play with a fake station and return the args passed to Popen."""
    station = {"name": "Test", "url_resolved": url, "url": "", "stationuuid": "abc"}
    captured = []

    def fake_popen(args, **kwargs):
        captured.append(args)
        raise FileNotFoundError  # abort after capture

    with patch.object(app, "_stop_player"):
        with patch.object(app, "_notify_click"):
            with patch.object(app, "_track_song_title"):
                with patch.object(app, "_set_status"):
                    with patch("subprocess.Popen", side_effect=fake_popen):
                        app._play(station)

    return captured[0] if captured else None


def test_normal_url_has_separator(app):
    args = _captured_args(app, "http://stream.example.com/radio")
    assert "--" in args
    url_index = args.index("--") + 1
    assert args[url_index] == "http://stream.example.com/radio"


def test_option_like_url_is_not_treated_as_option(app):
    malicious_url = "--script=/tmp/evil.lua"
    args = _captured_args(app, malicious_url)
    assert "--" in args
    # The -- sentinel must appear before the malicious string
    separator_index = args.index("--")
    url_index = args.index(malicious_url)
    assert separator_index < url_index, "-- must precede the URL"


def test_separator_comes_after_named_options(app):
    args = _captured_args(app, "http://stream.example.com/radio")
    separator_index = args.index("--")
    # All mpv named options must appear before --
    for arg in args[1:separator_index]:
        assert arg.startswith("--"), f"Unexpected positional arg before --: {arg}"
