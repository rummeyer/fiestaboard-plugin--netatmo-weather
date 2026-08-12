"""Shared fixtures for the Netatmo plugin tests.

Nothing here touches the network: the station payload is a hand-built copy of
what ``getstationsdata`` returns, and the plugin fixture stubs the one method
that speaks HTTP.
"""

import json
import pathlib

import pytest

from plugins.netatmo_weather import NetatmoWeatherPlugin

MANIFEST = json.loads((pathlib.Path(__file__).resolve().parent.parent / "manifest.json").read_text())

# 2026-08-12 12:35 UTC — 14:35 in Berlin, the moment the committed previews
# were rendered at.
MEASURED_AT = 1786538100

# Module ids, so tests can select and reorder without repeating the strings.
MAIN_ID = "70:ee:50:00:00:01"
KITCHEN_ID = "02:00:00:00:00:02"
BEDROOM_ID = "03:00:00:00:00:03"
OUTDOOR_ID = "02:00:00:00:00:04"
RAIN_ID = "05:00:00:00:00:05"
WIND_ID = "06:00:00:00:00:06"


def station_body():
    """A two-room station with an outdoor, a rain and a wind module.

    Room names carry umlauts on purpose — Netatmo module names are whatever the
    owner typed into the app, and the board has no umlaut tiles.
    """
    return {
        "devices": [
            {
                "_id": MAIN_ID,
                "type": "NAMain",
                "station_name": "Zuhause",
                "module_name": "Wohnzimmer",
                "reachable": True,
                "wifi_status": 55,
                "dashboard_data": {
                    "time_utc": MEASURED_AT,
                    "Temperature": 21.5,
                    "CO2": 612,
                    "Humidity": 45,
                    "Noise": 37,
                    "Pressure": 1017.3,
                    "AbsolutePressure": 939.7,
                    "min_temp": 20.4,
                    "max_temp": 22.6,
                    "temp_trend": "up",
                    "pressure_trend": "down",
                },
                "modules": [
                    {
                        "_id": KITCHEN_ID,
                        "type": "NAModule4",
                        "module_name": "Küche",
                        "reachable": True,
                        "battery_percent": 78,
                        "dashboard_data": {
                            "time_utc": MEASURED_AT - 60,
                            "Temperature": 22.1,
                            "CO2": 843,
                            "Humidity": 41,
                            "min_temp": 21.0,
                            "max_temp": 23.4,
                            "temp_trend": "stable",
                        },
                    },
                    {
                        "_id": BEDROOM_ID,
                        "type": "NAModule4",
                        "module_name": "Schlafzimmer",
                        "reachable": True,
                        "battery_percent": 64,
                        "dashboard_data": {
                            "time_utc": MEASURED_AT - 120,
                            "Temperature": 19.4,
                            "CO2": 1180,
                            "Humidity": 52,
                            "temp_trend": "down",
                        },
                    },
                    {
                        "_id": OUTDOOR_ID,
                        "type": "NAModule1",
                        "module_name": "Draußen",
                        "reachable": True,
                        "battery_percent": 91,
                        "dashboard_data": {
                            "time_utc": MEASURED_AT - 30,
                            "Temperature": 8.4,
                            "Humidity": 78,
                            "min_temp": 3.6,
                            "max_temp": 11.2,
                            "temp_trend": "down",
                        },
                    },
                    {
                        "_id": RAIN_ID,
                        "type": "NAModule3",
                        "module_name": "Regen",
                        "reachable": True,
                        "battery_percent": 55,
                        "dashboard_data": {
                            "time_utc": MEASURED_AT - 30,
                            "Rain": 0.2,
                            "sum_rain_1": 0.4,
                            "sum_rain_24": 6.9,
                        },
                    },
                    {
                        "_id": WIND_ID,
                        "type": "NAModule2",
                        "module_name": "Wind",
                        "reachable": True,
                        "battery_percent": 47,
                        "dashboard_data": {
                            "time_utc": MEASURED_AT - 30,
                            "WindStrength": 12,
                            "WindAngle": 217,
                            "GustStrength": 27,
                            "GustAngle": 206,
                        },
                    },
                ],
            }
        ],
        "user": {"administrative": {"unit": 0}},
    }


@pytest.fixture
def manifest():
    """The real manifest — the plugin reads its id, refresh bounds and schema."""
    return MANIFEST


@pytest.fixture
def make_plugin(manifest, monkeypatch, tmp_path):
    """Build a configured plugin whose station data comes from the fixture.

    The token store is redirected into the test's own directory so a test run
    can never read or write a real FiestaBoard install's cached token.
    """
    monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "tokens.json"))

    def _make(body=None, **config):
        plugin = NetatmoWeatherPlugin(manifest)
        plugin.config = {
            "client_id": "id",
            "client_secret": "secret",
            "refresh_token": "seed",
            "timezone": "Europe/Berlin",
            **config,
        }
        payload = station_body() if body is None else body
        monkeypatch.setattr(plugin, "station_body", lambda *args, **kwargs: payload)
        return plugin

    return _make
