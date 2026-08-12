"""Tests for the Netatmo plugin."""

import json
import time

import pytest
import requests
from src.devices import BoardContext
from src.plugins.base import OptionsRequest, OptionsUnavailable

from plugins import netatmo_weather
from plugins.netatmo_weather import (
    NetatmoError,
    NetatmoWeatherPlugin,
    Reading,
    apply_custom_names,
    compass,
    convert_pressure,
    convert_rain,
    convert_temperature,
    convert_wind,
    degree_sign,
    module_row,
    number,
    parse_stations,
    read_stored_token,
    select_readings,
    to_board_text,
    token_key,
    write_stored_token,
)

from .conftest import (
    BEDROOM_ID,
    KITCHEN_ID,
    MAIN_ID,
    OUTDOOR_ID,
    RAIN_ID,
    WIND_ID,
    station_body,
)

FLAGSHIP = BoardContext.from_device_type("flagship")
NOTE = BoardContext.from_device_type("note")


class FakeResponse:
    """Just enough of ``requests.Response`` for the two calls the plugin makes."""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def indoor_reading(**overrides):
    """A living-room reading, with fields overridden per test."""
    defaults = {
        "module_id": "1",
        "name": "Wohnzimmer",
        "kind": "indoor",
        "station": "Zuhause",
        "temperature": 21.5,
        "humidity": 45,
        "co2": 612,
        "temp_trend": "up",
        "battery": 78,
    }
    return Reading(**{**defaults, **overrides})


class TestParsing:
    def test_every_module_of_the_station_becomes_a_reading(self):
        readings = parse_stations(station_body())
        assert [r.module_id for r in readings] == [MAIN_ID, KITCHEN_ID, BEDROOM_ID, OUTDOOR_ID, RAIN_ID, WIND_ID]

    def test_the_main_unit_comes_first_and_is_marked_as_such(self):
        readings = parse_stations(station_body())
        assert readings[0].is_main
        assert not any(r.is_main for r in readings[1:])

    def test_module_types_map_to_kinds(self):
        kinds = {r.module_id: r.kind for r in parse_stations(station_body())}
        assert kinds[MAIN_ID] == "indoor"
        assert kinds[KITCHEN_ID] == "indoor"
        assert kinds[OUTDOOR_ID] == "outdoor"
        assert kinds[RAIN_ID] == "rain"
        assert kinds[WIND_ID] == "wind"

    def test_measurements_are_read_off_dashboard_data(self):
        main = parse_stations(station_body())[0]
        assert (main.temperature, main.humidity, main.co2, main.noise) == (21.5, 45, 612, 37)
        assert (main.pressure, main.pressure_trend) == (1017.3, "down")

    def test_rain_and_wind_carry_their_own_fields(self):
        readings = {r.kind: r for r in parse_stations(station_body())}
        assert (readings["rain"].rain_hour, readings["rain"].rain_today) == (0.4, 6.9)
        assert (readings["wind"].wind, readings["wind"].gust, readings["wind"].wind_angle) == (12, 27, 217)

    def test_a_module_that_has_never_reported_still_gets_a_row(self):
        """``dashboard_data`` is absent, not empty, for a module that never reported.

        It is still a module the user owns, so it stays in the list with empty
        measurements rather than disappearing from the board silently.
        """
        body = station_body()
        body["devices"][0]["modules"][0].pop("dashboard_data")
        kitchen = next(r for r in parse_stations(body) if r.module_id == KITCHEN_ID)
        assert kitchen.temperature is None
        assert kitchen.name == "Küche"

    def test_module_types_the_plugin_cannot_read_are_skipped(self):
        """A thermostat rides the same endpoint shape but measures nothing here."""
        body = station_body()
        body["devices"][0]["modules"].append({"_id": "x", "type": "NATherm1", "module_name": "Heizung"})
        assert all(r.module_id != "x" for r in parse_stations(body))

    def test_station_name_falls_back_to_the_home_name(self):
        """Newer stations only carry home_name; older ones only station_name."""
        body = station_body()
        del body["devices"][0]["station_name"]
        body["devices"][0]["home_name"] = "Ferienhaus"
        assert parse_stations(body)[0].station == "Ferienhaus"

    def test_an_unnamed_main_unit_borrows_the_station_name(self):
        body = station_body()
        del body["devices"][0]["module_name"]
        assert parse_stations(body)[0].name == "Zuhause"

    def test_an_unnamed_add_on_module_falls_back_to_what_it_measures(self):
        body = station_body()
        del body["devices"][0]["modules"][2]["module_name"]
        assert next(r for r in parse_stations(body) if r.module_id == OUTDOOR_ID).name == "OUTDOOR"

    def test_a_station_with_no_name_at_all_still_has_one(self):
        """Favourite stations shared by other people can carry neither name."""
        body = station_body()
        del body["devices"][0]["station_name"]
        del body["devices"][0]["module_name"]
        assert parse_stations(body)[0].station == "NETATMO"

    def test_an_empty_body_yields_nothing(self):
        assert parse_stations({}) == []


class TestSelection:
    def test_no_selection_shows_everything(self):
        readings = parse_stations(station_body())
        assert select_readings(readings, []) == readings
        assert select_readings(readings, None) == readings

    def test_selection_is_rendered_in_the_users_order(self):
        readings = parse_stations(station_body())
        chosen = select_readings(readings, [OUTDOOR_ID, MAIN_ID])
        assert [r.module_id for r in chosen] == [OUTDOOR_ID, MAIN_ID]

    def test_a_module_that_no_longer_exists_is_skipped(self):
        """Modules get sold, moved to another account, or replaced."""
        readings = parse_stations(station_body())
        chosen = select_readings(readings, [MAIN_ID, "gone", OUTDOOR_ID])
        assert [r.module_id for r in chosen] == [MAIN_ID, OUTDOOR_ID]

    def test_custom_names_replace_the_netatmo_ones(self):
        readings = apply_custom_names(parse_stations(station_body()), {BEDROOM_ID: "Schlafen"})
        assert next(r for r in readings if r.module_id == BEDROOM_ID).name == "Schlafen"

    def test_a_blank_custom_name_leaves_the_netatmo_one(self):
        readings = apply_custom_names(parse_stations(station_body()), {BEDROOM_ID: "  "})
        assert next(r for r in readings if r.module_id == BEDROOM_ID).name == "Schlafzimmer"


class TestUnits:
    @pytest.mark.parametrize(("celsius", "expected"), [(0, 32), (21.5, 70.7), (-10, 14)])
    def test_temperature_converts_to_fahrenheit(self, celsius, expected):
        assert convert_temperature(celsius, "imperial") == pytest.approx(expected)

    def test_metric_temperature_is_left_alone(self):
        assert convert_temperature(21.5, "metric") == 21.5

    def test_rain_wind_and_pressure_convert(self):
        assert convert_rain(25.4, "imperial") == pytest.approx(1.0)
        assert convert_wind(16.09344, "imperial") == pytest.approx(10.0)
        assert convert_pressure(1013.25, "imperial") == pytest.approx(29.921, abs=0.01)

    def test_a_missing_measurement_stays_missing(self):
        assert convert_temperature(None, "imperial") is None
        assert convert_rain(None, "metric") is None
        assert convert_wind(None, "metric") is None
        assert convert_pressure(None, "metric") is None

    def test_number_rounds_to_the_requested_decimals(self):
        assert number(21.47, 1) == "21.5"
        assert number(21.47, 0) == "21"

    def test_number_of_nothing_is_empty_not_a_placeholder(self):
        assert number(None) == ""

    @pytest.mark.parametrize(
        ("angle", "expected"),
        [(0, "N"), (45, "NE"), (217, "SW"), (350, "N"), (360, "N")],
    )
    def test_wind_angle_becomes_a_compass_point(self, angle, expected):
        assert compass(angle) == expected

    def test_an_unreported_wind_angle_has_no_direction(self):
        assert compass(None) == ""
        assert compass(-1) == ""

    def test_umlauts_are_expanded_for_a_board_that_has_no_umlaut_tiles(self):
        assert to_board_text("Küche") == "KUECHE"
        assert to_board_text("Küche", "strip") == "KUCHE"
        assert to_board_text("Draußen") == "DRAUSSEN"

    def test_characters_the_board_cannot_flap_are_dropped(self):
        """Dropped, not substituted — an unknown glyph leaves a gap, not a guess."""
        assert to_board_text("Büro ➜ 21°") == "BUERO  21°"


class TestDegreeSign:
    """Tile 62 is ° on a Flagship and a heart on a Note — the same code."""

    def test_auto_keeps_the_sign_on_a_flagship(self):
        assert degree_sign("auto", "flagship") == "°"

    @pytest.mark.parametrize("device_type", ["note", "note_array"])
    def test_auto_drops_the_sign_on_note_shaped_boards(self, device_type):
        assert degree_sign("auto", device_type) == ""

    def test_always_keeps_it_even_where_it_prints_a_heart(self):
        assert degree_sign("on", "note") == "°"

    def test_never_drops_it_even_on_a_flagship(self):
        assert degree_sign("off", "flagship") == ""

    def test_an_unknown_board_is_treated_as_a_flagship(self):
        assert degree_sign("auto", None) == "°"


class TestRows:
    def row(self, reading=None, width=22, sign="°", **kwargs):
        options = {"secondary": "humidity", "trend": False, **kwargs}
        return module_row(reading or indoor_reading(), width, "metric", 1, sign, **options)

    def test_name_left_reading_right_filling_the_board_width(self):
        assert self.row() == "WOHNZIMMER   21.5° 45%"
        assert len(self.row()) == 22

    def test_the_trend_mark_is_appended_when_asked_for(self):
        assert self.row(trend=True) == "WOHNZIMMER 21.5° + 45%"

    def test_co2_and_battery_are_alternative_second_readings(self):
        office = indoor_reading(name="Büro")
        assert self.row(office, secondary="co2").endswith("21.5° 612PPM")
        assert self.row(office, secondary="battery").endswith("21.5° 78%")
        assert self.row(office, secondary="none").endswith("21.5°")

    def test_a_narrow_board_drops_the_extras_before_it_cuts_the_name(self):
        """A Note is 15 wide: dropping the humidity beats shortening the name.

        The sign is empty because a Note prints tile 62 as a heart — that is
        the same board this row is being sized for.
        """
        assert self.row(width=15, sign="", trend=True) == "WOHNZIMMER 21.5"

    def test_the_name_is_cut_only_when_the_bare_reading_leaves_no_room(self):
        assert self.row(width=12) == "WOHNZI 21.5°"

    def test_a_module_with_no_reading_keeps_its_name_on_the_board(self):
        """A quiet sensor is still a sensor; the row says which one went quiet."""
        assert self.row(indoor_reading(temperature=None)) == "WOHNZIMMER            "

    def test_a_rain_gauge_shows_the_last_24_hours(self):
        rain = Reading(module_id="r", name="Regen", kind="rain", station="Z", rain_today=6.9)
        assert self.row(rain) == "REGEN            6.9MM"

    def test_a_wind_gauge_shows_speed_and_direction(self):
        wind = Reading(module_id="w", name="Wind", kind="wind", station="Z", wind=12, wind_angle=217)
        assert self.row(wind) == "WIND          12KMH SW"

    def test_a_wind_gauge_that_has_not_reported_shows_nothing(self):
        wind = Reading(module_id="w", name="Wind", kind="wind", station="Z")
        assert self.row(wind).strip() == "WIND"

    def test_a_reading_wider_than_the_board_pushes_the_name_off_it(self):
        """Nothing sensible is left to show, but the number stays whole."""
        wind = Reading(module_id="w", name="Wind", kind="wind", station="Z", wind=12, wind_angle=217)
        assert self.row(wind, width=8) == "12KMH SW"

    def test_imperial_rows_carry_imperial_units(self):
        rain = Reading(module_id="r", name="Rain", kind="rain", station="Z", rain_today=25.4)
        assert module_row(rain, 22, "imperial", 1, "°", secondary="none", trend=False).endswith("1.0IN")


class TestFetchData:
    def test_a_configured_plugin_renders_every_module(self, make_plugin):
        result = make_plugin().get_data(FLAGSHIP)
        assert result.available
        assert result.formatted_lines[0] == "WOHNZIMMER   21.5° 45%"

    def test_the_layout_matches_the_board_it_renders_on(self, make_plugin):
        plugin = make_plugin()
        flagship = plugin.get_data(FLAGSHIP).formatted_lines
        note = plugin.get_data(NOTE).formatted_lines
        assert [len(row) for row in flagship] == [22] * 6
        assert [len(row) for row in note] == [15] * 3

    def test_modules_past_the_boards_height_fall_off_the_end(self, make_plugin):
        """Six modules do not fit a Note, and the user's order decides which three do."""
        note = make_plugin(modules=[OUTDOOR_ID, MAIN_ID, RAIN_ID, KITCHEN_ID]).get_data(NOTE).formatted_lines
        assert [row.split()[0] for row in note] == ["DRAUSSEN", "WOHNZIMMER", "REGEN"]

    def test_the_named_variables_pick_the_right_modules(self, make_plugin):
        data = make_plugin().get_data(FLAGSHIP).data
        assert data["indoor_name"] == "WOHNZIMMER"
        assert data["indoor_temp"] == "21.5"
        assert data["indoor_co2"] == "612"
        assert data["indoor_noise"] == "37"
        assert data["outdoor_name"] == "DRAUSSEN"
        assert data["outdoor_temp"] == "8.4"
        assert (data["outdoor_min"], data["outdoor_max"]) == ("3.6", "11.2")
        assert (data["rain_hour"], data["rain_today"]) == ("0.4", "6.9")
        assert (data["wind"], data["wind_gust"], data["wind_from"]) == ("12", "27", "SW")

    def test_trends_render_as_board_characters(self, make_plugin):
        data = make_plugin().get_data(FLAGSHIP).data
        assert (data["indoor_trend"], data["outdoor_trend"], data["pressure_trend"]) == ("+", "-", "-")

    def test_the_measurement_time_is_shown_in_the_configured_timezone(self, make_plugin):
        berlin = make_plugin().get_data(FLAGSHIP).data["updated"]
        utc = make_plugin(timezone="UTC").get_data(FLAGSHIP).data["updated"]
        assert (berlin, utc) == ("14:35", "12:35")

    def test_the_block_variable_is_the_whole_board(self, make_plugin):
        result = make_plugin().get_data(FLAGSHIP)
        assert result.data["block"].split("\n") == result.formatted_lines

    def test_line_variables_mirror_the_rows_at_full_board_width(self, make_plugin):
        result = make_plugin().get_data(NOTE)
        assert result.data["line1"] == result.formatted_lines[0]
        # A Note has three rows; the remaining line variables exist but are empty.
        assert result.data["line4"] == ""

    def test_the_module_array_carries_one_entry_per_rendered_module(self, make_plugin):
        modules = make_plugin(modules=[MAIN_ID, OUTDOOR_ID]).get_data(FLAGSHIP).data["modules"]
        assert [m["name"] for m in modules] == ["WOHNZIMMER", "DRAUSSEN"]
        assert modules[0]["temp"] == "21.5"
        assert modules[0]["battery"] == ""
        assert modules[1]["battery"] == "91"

    def test_imperial_converts_every_measurement(self, make_plugin):
        data = make_plugin(units="imperial").get_data(FLAGSHIP).data
        assert data["indoor_temp"] == "70.7"
        assert data["outdoor_temp"] == "47.1"
        assert data["temp_unit"] == "°F"

    def test_a_note_gets_no_degree_sign_by_default(self, make_plugin):
        plugin = make_plugin()
        assert plugin.get_data(NOTE).data["temp_unit"] == "C"
        assert plugin.get_data(FLAGSHIP).data["temp_unit"] == "°C"

    def test_custom_names_reach_the_board(self, make_plugin):
        plugin = make_plugin(modules=[BEDROOM_ID], module_names={BEDROOM_ID: "Schlafen"})
        assert plugin.get_data(FLAGSHIP).formatted_lines[0].startswith("SCHLAFEN")

    def test_without_credentials_the_plugin_reports_itself_unavailable(self, make_plugin, monkeypatch):
        for name in ("NETATMO_CLIENT_ID", "NETATMO_CLIENT_SECRET", "NETATMO_REFRESH_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        result = make_plugin(client_id="", client_secret="", refresh_token="").get_data(FLAGSHIP)
        assert not result.available
        assert "not configured" in result.error

    def test_credentials_may_come_from_the_environment(self, make_plugin, monkeypatch):
        monkeypatch.setenv("NETATMO_CLIENT_ID", "env-id")
        monkeypatch.setenv("NETATMO_CLIENT_SECRET", "env-secret")
        monkeypatch.setenv("NETATMO_REFRESH_TOKEN", "env-token")
        plugin = make_plugin(client_id="", client_secret="", refresh_token="")
        assert plugin.credentials() == ("env-id", "env-secret", "env-token")
        assert plugin.get_data(FLAGSHIP).available

    def test_an_api_failure_is_reported_not_raised(self, make_plugin, monkeypatch):
        plugin = make_plugin()
        monkeypatch.setattr(plugin, "station_body", _raise(NetatmoError("Netatmo unreachable: timeout")))
        result = plugin.get_data(FLAGSHIP)
        assert not result.available
        assert result.error == "Netatmo unreachable: timeout"

    def test_an_unexpected_payload_does_not_break_the_render_loop(self, make_plugin, monkeypatch):
        plugin = make_plugin()
        monkeypatch.setattr(plugin, "station_body", _raise(TypeError("nonsense")))
        assert not plugin.get_data(FLAGSHIP).available

    def test_an_account_without_a_station_says_so(self, make_plugin):
        result = make_plugin(body={"devices": []}).get_data(FLAGSHIP)
        assert not result.available
        assert "no weather station" in result.error


class TestValidateConfig:
    def test_an_untouched_plugin_validates(self, manifest):
        """The shipped defaults are empty credentials; that is not an error."""
        assert NetatmoWeatherPlugin(manifest).validate_config({}) == []

    def test_half_filled_credentials_are_rejected(self, manifest):
        errors = NetatmoWeatherPlugin(manifest).validate_config({"client_id": "id"})
        assert len(errors) == 1
        assert "client_secret" in errors[0] and "refresh_token" in errors[0]

    def test_a_complete_credential_set_validates(self, manifest):
        config = {"client_id": "id", "client_secret": "secret", "refresh_token": "token"}
        assert NetatmoWeatherPlugin(manifest).validate_config(config) == []

    def test_an_unresolvable_timezone_is_rejected(self, manifest):
        assert NetatmoWeatherPlugin(manifest).validate_config({"timezone": "Mars/Olympus"})

    def test_an_empty_timezone_means_follow_fiestaboard(self, manifest):
        assert NetatmoWeatherPlugin(manifest).validate_config({"timezone": ""}) == []

    def test_a_timezone_that_is_not_even_a_string_is_rejected(self, manifest):
        assert NetatmoWeatherPlugin(manifest).validate_config({"timezone": 42})

    def test_modules_must_be_a_list(self, manifest):
        assert NetatmoWeatherPlugin(manifest).validate_config({"modules": "all"})

    def test_a_timezone_that_disappears_after_saving_falls_back_to_utc(self, make_plugin):
        """Validation happens on save; the tz database can change under a running board."""
        plugin = make_plugin()
        plugin.config = {**plugin.config, "timezone": "Mars/Olympus"}
        assert plugin.get_data(FLAGSHIP).data["updated"] == "12:35"

    def test_a_refresh_below_the_manifest_floor_is_rejected(self, manifest):
        assert NetatmoWeatherPlugin(manifest)._validate_refresh_seconds({"refresh_seconds": 30})


class TestTokenStore:
    """Netatmo retires a refresh token the moment it is used."""

    def test_a_rotated_token_survives_a_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "tokens.json"))
        key = token_key("id", "seed")
        write_stored_token(key, "rotated")
        assert read_stored_token(key) == "rotated"

    def test_a_new_seed_token_starts_a_new_chain(self, tmp_path, monkeypatch):
        """Re-pasting the token in the UI is how a user fixes a broken chain."""
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "tokens.json"))
        write_stored_token(token_key("id", "old-seed"), "rotated")
        assert read_stored_token(token_key("id", "new-seed")) == ""

    def test_different_credentials_do_not_share_an_entry(self):
        assert token_key("id-a", "seed") != token_key("id-b", "seed")

    def test_the_cache_file_is_not_world_readable(self, tmp_path, monkeypatch):
        path = tmp_path / "tokens.json"
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(path))
        write_stored_token(token_key("id", "seed"), "rotated")
        assert path.stat().st_mode & 0o077 == 0

    def test_a_second_token_does_not_evict_the_first(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "tokens.json"))
        write_stored_token(token_key("id", "a"), "token-a")
        write_stored_token(token_key("id", "b"), "token-b")
        assert read_stored_token(token_key("id", "a")) == "token-a"

    def test_a_corrupt_cache_file_reads_as_empty(self, tmp_path, monkeypatch):
        path = tmp_path / "tokens.json"
        path.write_text("{not json")
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(path))
        assert read_stored_token(token_key("id", "seed")) == ""

    def test_a_corrupt_cache_file_is_replaced_rather_than_appended_to(self, tmp_path, monkeypatch):
        path = tmp_path / "tokens.json"
        path.write_text("{not json")
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(path))
        write_stored_token(token_key("id", "seed"), "rotated")
        assert json.loads(path.read_text()) == {token_key("id", "seed"): "rotated"}

    def test_an_unwritable_directory_costs_a_warning_not_the_render(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "missing" / "tokens.json"))
        monkeypatch.setattr(netatmo_weather.Path, "mkdir", _raise(OSError("read-only")))
        write_stored_token("key", "rotated")
        assert "Could not cache" in caplog.text

    def test_the_default_location_sits_beside_fiestaboards_own_config(self, monkeypatch):
        monkeypatch.delenv("NETATMO_TOKEN_STORE", raising=False)
        assert netatmo_weather.token_store_path().name == "netatmo_weather_tokens.json"


class TestAuthentication:
    def plugin(self, manifest, tmp_path, monkeypatch, **config):
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "tokens.json"))
        plugin = NetatmoWeatherPlugin(manifest)
        plugin.config = {"client_id": "id", "client_secret": "secret", "refresh_token": "seed", **config}
        return plugin

    def test_the_rotated_refresh_token_is_kept_for_next_time(self, manifest, tmp_path, monkeypatch):
        posted = []

        def fake_post(url, data=None, timeout=None):
            posted.append(data["refresh_token"])
            return FakeResponse(200, {"access_token": "access-1", "refresh_token": "rotated-1", "expires_in": 10800})

        monkeypatch.setattr(netatmo_weather.requests, "post", fake_post)
        plugin = self.plugin(manifest, tmp_path, monkeypatch)

        assert plugin._token() == "access-1"
        assert posted == ["seed"]
        assert read_stored_token(token_key("id", "seed")) == "rotated-1"

    def test_the_access_token_is_reused_until_it_nears_expiry(self, manifest, tmp_path, monkeypatch):
        calls = []

        def fake_post(url, data=None, timeout=None):
            calls.append(data)
            return FakeResponse(200, {"access_token": "access", "refresh_token": "rotated", "expires_in": 10800})

        monkeypatch.setattr(netatmo_weather.requests, "post", fake_post)
        plugin = self.plugin(manifest, tmp_path, monkeypatch)

        plugin._token()
        plugin._token()
        assert len(calls) == 1

    def test_an_expired_access_token_is_renewed(self, manifest, tmp_path, monkeypatch):
        calls = []

        def fake_post(url, data=None, timeout=None):
            calls.append(data)
            return FakeResponse(200, {"access_token": "access", "refresh_token": "rotated", "expires_in": 10800})

        monkeypatch.setattr(netatmo_weather.requests, "post", fake_post)
        plugin = self.plugin(manifest, tmp_path, monkeypatch)

        plugin._token()
        plugin._access_expires_at = time.time() - 1
        plugin._token()
        assert len(calls) == 2

    def test_a_stale_cached_chain_falls_back_to_the_pasted_token(self, manifest, tmp_path, monkeypatch):
        """Another client refreshing the same app leaves our cached token dead."""
        monkeypatch.setenv("NETATMO_TOKEN_STORE", str(tmp_path / "tokens.json"))
        write_stored_token(token_key("id", "seed"), "stale")
        tried = []

        def fake_post(url, data=None, timeout=None):
            tried.append(data["refresh_token"])
            if data["refresh_token"] == "stale":
                return FakeResponse(400, {"error": "invalid_grant"})
            return FakeResponse(200, {"access_token": "access", "refresh_token": "fresh", "expires_in": 10800})

        monkeypatch.setattr(netatmo_weather.requests, "post", fake_post)
        plugin = self.plugin(manifest, tmp_path, monkeypatch)

        assert plugin._token() == "access"
        assert tried == ["stale", "seed"]

    def test_a_rejected_token_is_reported_with_netatmos_own_reason(self, manifest, tmp_path, monkeypatch):
        monkeypatch.setattr(
            netatmo_weather.requests,
            "post",
            lambda *a, **k: FakeResponse(400, {"error": "invalid_grant", "error_description": "token expired"}),
        )
        plugin = self.plugin(manifest, tmp_path, monkeypatch)
        with pytest.raises(NetatmoError, match="invalid_grant"):
            plugin._token()

    def test_a_network_failure_is_reported_as_unreachable(self, manifest, tmp_path, monkeypatch):
        monkeypatch.setattr(netatmo_weather.requests, "post", _raise(requests.ConnectionError("no route")))
        plugin = self.plugin(manifest, tmp_path, monkeypatch)
        with pytest.raises(NetatmoError, match="unreachable"):
            plugin._token()

    def test_a_revoked_access_token_is_renewed_once_before_giving_up(self, manifest, tmp_path, monkeypatch):
        """Netatmo can revoke a token before its stated lifetime is up."""
        monkeypatch.setattr(
            netatmo_weather.requests,
            "post",
            lambda *a, **k: FakeResponse(200, {"access_token": "access", "refresh_token": "r", "expires_in": 10800}),
        )
        attempts = []

        def fake_get(url, params=None, headers=None, timeout=None):
            attempts.append(headers["Authorization"])
            if len(attempts) == 1:
                return FakeResponse(403, {"error": {"code": 2, "message": "Invalid access token"}})
            return FakeResponse(200, {"body": station_body()})

        monkeypatch.setattr(netatmo_weather.requests, "get", fake_get)
        plugin = self.plugin(manifest, tmp_path, monkeypatch)

        assert plugin.station_body()["devices"]
        assert len(attempts) == 2

    def test_a_token_refused_twice_reports_netatmos_reason(self, manifest, tmp_path, monkeypatch):
        """One retry, then the error stands — a revoked app never recovers by looping."""
        monkeypatch.setattr(
            netatmo_weather.requests,
            "post",
            lambda *a, **k: FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 10800}),
        )
        monkeypatch.setattr(
            netatmo_weather.requests,
            "get",
            lambda *a, **k: FakeResponse(403, {"error": {"code": 2, "message": "Invalid access token"}}),
        )
        plugin = self.plugin(manifest, tmp_path, monkeypatch)
        with pytest.raises(NetatmoError, match="Invalid access token"):
            plugin.station_body()

    def test_an_api_error_carries_netatmos_message(self, manifest, tmp_path, monkeypatch):
        monkeypatch.setattr(
            netatmo_weather.requests,
            "post",
            lambda *a, **k: FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 10800}),
        )
        monkeypatch.setattr(
            netatmo_weather.requests,
            "get",
            lambda *a, **k: FakeResponse(429, {"error": {"code": 26, "message": "User usage reached"}}),
        )
        plugin = self.plugin(manifest, tmp_path, monkeypatch)
        with pytest.raises(NetatmoError, match="User usage reached"):
            plugin.station_body()

    def test_a_non_json_error_falls_back_to_the_status_code(self, manifest, tmp_path, monkeypatch):
        monkeypatch.setattr(
            netatmo_weather.requests,
            "post",
            lambda *a, **k: FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 10800}),
        )
        monkeypatch.setattr(netatmo_weather.requests, "get", lambda *a, **k: FakeResponse(502, None))
        plugin = self.plugin(manifest, tmp_path, monkeypatch)
        with pytest.raises(NetatmoError, match="HTTP 502"):
            plugin.station_body()

    def test_a_network_failure_while_reading_the_station_is_reported(self, manifest, tmp_path, monkeypatch):
        monkeypatch.setattr(
            netatmo_weather.requests,
            "post",
            lambda *a, **k: FakeResponse(200, {"access_token": "a", "refresh_token": "r", "expires_in": 10800}),
        )
        monkeypatch.setattr(netatmo_weather.requests, "get", _raise(requests.Timeout("slow")))
        plugin = self.plugin(manifest, tmp_path, monkeypatch)
        with pytest.raises(NetatmoError, match="unreachable"):
            plugin.station_body()


class TestOptions:
    """The module picker runs while the settings dialog is open."""

    def test_every_module_is_offered_grouped_by_station(self, make_plugin):
        options = make_plugin().get_options(OptionsRequest(options_id="modules"))
        assert [option.value for option in options] == [MAIN_ID, KITCHEN_ID, BEDROOM_ID, OUTDOOR_ID, RAIN_ID, WIND_ID]
        assert {option.group for option in options} == {"Zuhause"}

    def test_the_current_reading_tells_two_identical_rooms_apart(self, make_plugin):
        options = make_plugin().get_options(OptionsRequest(options_id="modules"))
        assert "21.5°" in options[0].description
        assert options[0].preview == "WOHNZIMMER 21.5°"

    def test_an_unconfigured_plugin_asks_for_credentials_instead_of_failing(self, make_plugin, monkeypatch):
        for name in ("NETATMO_CLIENT_ID", "NETATMO_CLIENT_SECRET", "NETATMO_REFRESH_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        plugin = make_plugin(client_id="", client_secret="", refresh_token="")
        with pytest.raises(OptionsUnavailable, match="client id"):
            plugin.get_options(OptionsRequest(options_id="modules"))

    def test_an_unreachable_api_is_a_hint_not_a_stack_trace(self, make_plugin, monkeypatch):
        plugin = make_plugin()
        monkeypatch.setattr(plugin, "station_body", _raise(NetatmoError("Netatmo unreachable: timeout")))
        with pytest.raises(OptionsUnavailable, match="unreachable"):
            plugin.get_options(OptionsRequest(options_id="modules"))

    def test_a_catalog_the_plugin_does_not_serve_is_refused(self, make_plugin):
        with pytest.raises(NotImplementedError):
            make_plugin().get_options(OptionsRequest(options_id="stations"))


def _raise(error):
    """A stub that raises *error* whatever it is called with."""

    def _stub(*args, **kwargs):
        raise error

    return _stub
