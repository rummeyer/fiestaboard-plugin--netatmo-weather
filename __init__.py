"""Netatmo plugin for FiestaBoard.

Puts the readings of a Netatmo Weather Station on the board — living room
temperature, outdoor temperature, CO2, humidity, rain, wind — as one row per
module, laid out for the board it is rendering on so the same plugin fits a
Flagship (22x6) and a Note (15x3).

The Netatmo API speaks OAuth2 and hands out a *rotating* refresh token: every
refresh invalidates the one that was used. FiestaBoard gives plugins no way to
write their own config back, so the rotated token is kept in a small cache file
next to FiestaBoard's ``config.json`` (see :func:`token_store_path`). The token
pasted into the settings stays the seed of that chain and is never overwritten.
"""

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from src.plugins.base import (
    Option,
    OptionsRequest,
    OptionsResult,
    OptionsUnavailable,
    PluginBase,
    PluginResult,
)

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.netatmo.com/oauth2/token"
STATION_URL = "https://api.netatmo.com/api/getstationsdata"

# Netatmo allows 500 calls per hour per user. The station itself only measures
# every five minutes, so anything below that is wasted quota.
HTTP_TIMEOUT = 10
# A settings dialog is a person waiting, so the options call gets a shorter one.
OPTIONS_TIMEOUT = 8
# Renew the access token this long before it actually expires, so a fetch that
# starts just under the wire does not race the expiry.
TOKEN_EXPIRY_MARGIN = 120

DEFAULT_TIMEZONE = "UTC"

# Board geometry assumed outside a board-scoped render (unit tests, the
# /plugins/{id}/data endpoint). Flagship is the wider shape, so a layout built
# for it never silently loses a module.
FALLBACK_WIDTH = 22
FALLBACK_HEIGHT = 6

# Characters the board can flap. Anything else is dropped rather than sent as a
# wrong tile. Reference: src/board_chars.py in FiestaBoard.
BOARD_CHARSET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$()-+&=;:'\"%,./?°")

# Netatmo room names are whatever the owner typed — "Küche", "Büro", "Draußen".
UMLAUT_EXPAND = {"Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS"}
UMLAUT_STRIP = {"Ä": "A", "Ö": "O", "Ü": "U", "ß": "SS"}

# Tile 62 is ° on a Flagship and a heart on a Note — the same code, different
# print. "auto" therefore drops the degree sign on every note-shaped board.
DEGREE = "°"
NOTE_DEVICE_TYPES = ("note", "note_array")

# Netatmo device types. The main indoor unit is the station itself; everything
# else hangs off it as a radio module.
MAIN = "NAMain"
OUTDOOR_MODULE = "NAModule1"
WIND_MODULE = "NAModule2"
RAIN_MODULE = "NAModule3"
INDOOR_MODULE = "NAModule4"

KINDS = {
    MAIN: "indoor",
    INDOOR_MODULE: "indoor",
    OUTDOOR_MODULE: "outdoor",
    RAIN_MODULE: "rain",
    WIND_MODULE: "wind",
}

# temp_trend / pressure_trend as the board can render them.
TREND_MARKS = {"up": "+", "down": "-", "stable": "="}

UNITS = ("metric", "imperial")
DEFAULT_UNITS = "metric"

SECONDARY_CHOICES = ("none", "humidity", "co2", "battery")
DEFAULT_SECONDARY = "humidity"

DEGREE_CHOICES = ("auto", "on", "off")
DEFAULT_DEGREE = "auto"

# Compass points for the wind module, coarse enough to stay readable on a board.
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


class NetatmoError(Exception):
    """Netatmo answered, but not with data — bad credentials, revoked app, quota."""


@dataclass(frozen=True)
class Reading:
    """One module's latest measurements, in Netatmo's own (metric) units."""

    module_id: str
    name: str
    kind: str
    station: str
    is_main: bool = False
    reachable: bool = True
    measured_at: int | None = None
    temperature: float | None = None
    humidity: int | None = None
    co2: int | None = None
    noise: int | None = None
    pressure: float | None = None
    min_temp: float | None = None
    max_temp: float | None = None
    temp_trend: str = ""
    pressure_trend: str = ""
    rain_hour: float | None = None
    rain_today: float | None = None
    wind: int | None = None
    gust: int | None = None
    wind_angle: int | None = None
    battery: int | None = None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _station_name(device: dict[str, Any]) -> str:
    """Name of the station a module belongs to.

    Netatmo has renamed this field twice: older stations carry ``station_name``,
    newer ones only ``home_name``, and a station bound to a Netatmo *home* may
    carry both. Favourite stations shared by other people sometimes carry
    neither.
    """
    for field in ("station_name", "home_name"):
        value = device.get(field)
        if value:
            return str(value)
    return "NETATMO"


def _module_name(module: dict[str, Any], station: str, kind: str) -> str:
    """Display name for a module, falling back when the owner never named it."""
    name = module.get("module_name") or module.get("station_name")
    if name:
        return str(name)
    if module.get("type") == MAIN:
        return station
    return kind.upper()


def _reading(module: dict[str, Any], station: str, *, is_main: bool) -> Reading | None:
    """Turn one raw device/module dict into a :class:`Reading`.

    Returns ``None`` for module types this plugin does not read (thermostats
    and valves share the endpoint's shape but not its measurements).

    ``dashboard_data`` is absent — not empty — for a module that has not
    reported since the station was set up, so every measurement is optional
    and a module with no data still becomes a Reading. It is the difference
    between "the sensor is quiet" and "the sensor does not exist", and only the
    first should still take up a board row.
    """
    kind = KINDS.get(str(module.get("type", "")))
    if kind is None:
        return None
    data = module.get("dashboard_data") or {}
    return Reading(
        module_id=str(module.get("_id", "")),
        name=_module_name(module, station, kind),
        kind=kind,
        station=station,
        is_main=is_main,
        reachable=bool(module.get("reachable", True)),
        measured_at=data.get("time_utc"),
        temperature=data.get("Temperature"),
        humidity=data.get("Humidity"),
        co2=data.get("CO2"),
        noise=data.get("Noise"),
        pressure=data.get("Pressure"),
        min_temp=data.get("min_temp"),
        max_temp=data.get("max_temp"),
        temp_trend=str(data.get("temp_trend") or ""),
        pressure_trend=str(data.get("pressure_trend") or ""),
        rain_hour=data.get("sum_rain_1"),
        rain_today=data.get("sum_rain_24"),
        wind=data.get("WindStrength"),
        gust=data.get("GustStrength"),
        wind_angle=data.get("WindAngle"),
        battery=module.get("battery_percent"),
    )


def parse_stations(body: dict[str, Any]) -> list[Reading]:
    """Flatten a ``getstationsdata`` body into readings, station by station.

    The main unit comes first within each station, then its modules in the
    order Netatmo returns them — which is the order the app shows them in.
    """
    readings: list[Reading] = []
    for device in body.get("devices") or []:
        station = _station_name(device)
        main = _reading(device, station, is_main=True)
        if main is not None:
            readings.append(main)
        for module in device.get("modules") or []:
            reading = _reading(module, station, is_main=False)
            if reading is not None:
                readings.append(reading)
    return readings


def apply_custom_names(readings: list[Reading], custom: dict[str, Any]) -> list[Reading]:
    """Override module names with the short ones the user typed in the settings.

    "Wohnzimmer Erdgeschoss" is 23 tiles before a single degree is shown, so the
    module picker collects a board-sized name per chosen module. The keys are
    always the option value stringified, which for Netatmo is the module id.
    """
    if not custom:
        return readings
    renamed = []
    for reading in readings:
        name = str(custom.get(reading.module_id) or "").strip()
        renamed.append(replace(reading, name=name) if name else reading)
    return renamed


def select_readings(readings: list[Reading], chosen: list[str] | None) -> list[Reading]:
    """Pick the modules to render, in the order the user arranged them.

    An empty selection means "everything the account has" — a fresh install
    should show the station rather than an empty board, and the layout drops
    whatever does not fit anyway.
    """
    if not chosen:
        return readings
    by_id = {reading.module_id: reading for reading in readings}
    # Ids that no longer exist are skipped rather than rendered as a gap: a
    # module can be sold, moved to another account, or simply renamed away.
    return [by_id[module_id] for module_id in chosen if module_id in by_id]


# --------------------------------------------------------------------------
# Units and formatting
# --------------------------------------------------------------------------


def to_board_text(text: str, umlauts: str = "expand") -> str:
    """Uppercase *text* and reduce it to characters the board can display."""
    table = UMLAUT_STRIP if umlauts == "strip" else UMLAUT_EXPAND
    text = str(text).upper()
    for source, replacement in table.items():
        text = text.replace(source, replacement)
    return "".join(char for char in text if char in BOARD_CHARSET)


def convert_temperature(celsius: float | None, units: str) -> float | None:
    """Netatmo always reports Celsius, whatever the account is set to display."""
    if celsius is None:
        return None
    return celsius * 9 / 5 + 32 if units == "imperial" else celsius


def convert_rain(millimetres: float | None, units: str) -> float | None:
    """Rain arrives in millimetres; imperial wants inches."""
    if millimetres is None:
        return None
    return millimetres / 25.4 if units == "imperial" else millimetres


def convert_wind(kmh: float | None, units: str) -> float | None:
    """Wind arrives in km/h; imperial wants mph."""
    if kmh is None:
        return None
    return kmh / 1.609344 if units == "imperial" else kmh


def convert_pressure(mbar: float | None, units: str) -> float | None:
    """Pressure arrives in mbar; imperial wants inches of mercury."""
    if mbar is None:
        return None
    return mbar / 33.8639 if units == "imperial" else mbar


def number(value: float | None, decimals: int = 1) -> str:
    """Format a measurement for the board, or "" when the sensor said nothing.

    Empty rather than a placeholder: a template line that would read
    ``KITCHEN --`` is better left blank, and the row builder decides.
    """
    if value is None:
        return ""
    return f"{value:.{decimals}f}"


def compass(angle: int | None) -> str:
    """Wind direction as an eight-point compass label."""
    if angle is None or angle < 0:
        return ""
    return COMPASS[int((angle % 360) / 45 + 0.5) % 8]


def degree_sign(degree: str, device_type: str | None) -> str:
    """The degree sign, unless the board would print it as a heart.

    Tile 62 is ° on a Flagship and ❤ on a Note. "auto" keeps the sign where it
    prints correctly and drops it everywhere else; the explicit settings exist
    because a Note owner may well prefer the heart to no unit at all.
    """
    if degree == "off":
        return ""
    if degree == "on":
        return DEGREE
    return "" if (device_type or "").startswith(NOTE_DEVICE_TYPES) else DEGREE


def temperature_unit(units: str, sign: str) -> str:
    """Unit suffix for a temperature, e.g. ``°C``, ``C`` or ``°F``."""
    return f"{sign}{'F' if units == 'imperial' else 'C'}"


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def row_value(reading: Reading, units: str, decimals: int, sign: str, *, secondary: str, trend: bool) -> str:
    """The right-hand side of a module's row — what the module measures.

    Temperature for anything with a thermometer, rain for a rain gauge, wind
    for a wind gauge. The optional trend mark and secondary reading are
    appended here and dropped by :func:`module_row` when the row is too tight.
    """
    parts: list[str] = []
    if reading.kind == "rain":
        rain = number(convert_rain(reading.rain_today, units), 1)
        return f"{rain}{'IN' if units == 'imperial' else 'MM'}" if rain else ""
    if reading.kind == "wind":
        wind = number(convert_wind(reading.wind, units), 0)
        if not wind:
            return ""
        parts.append(f"{wind}{'MPH' if units == 'imperial' else 'KMH'}")
        direction = compass(reading.wind_angle)
        if direction:
            parts.append(direction)
        return " ".join(parts)

    temperature = number(convert_temperature(reading.temperature, units), decimals)
    if not temperature:
        return ""
    parts.append(f"{temperature}{sign}")
    if trend and reading.temp_trend in TREND_MARKS:
        parts.append(TREND_MARKS[reading.temp_trend])
    extra = secondary_value(reading, secondary)
    if extra:
        parts.append(extra)
    return " ".join(parts)


def secondary_value(reading: Reading, secondary: str) -> str:
    """The optional second measurement shown after the temperature."""
    if secondary == "humidity" and reading.humidity is not None:
        return f"{int(reading.humidity)}%"
    if secondary == "co2" and reading.co2 is not None:
        return f"{int(reading.co2)}PPM"
    if secondary == "battery" and reading.battery is not None:
        return f"{int(reading.battery)}%"
    return ""


def _compose(name: str, value: str, width: int) -> str:
    """Put a name and its reading on one row: name left, reading right.

    Only the name is ever shortened, and only once the reading alone has run
    out of room — a truncated "WOHNZI" is still recognisable, a truncated "21."
    is a lie.
    """
    if not value:
        return name[:width].ljust(width)
    room = max(width - len(value) - 1, 0)
    if not room:
        return value[:width].ljust(width)
    return f"{name[:room].ljust(room)} {value}"


def board_rows(
    readings: list[Reading],
    width: int,
    units: str,
    decimals: int,
    sign: str,
    *,
    secondary: str,
    trend: bool,
    umlauts: str = "expand",
) -> list[str]:
    """Render every module at the richest detail all of them can carry.

    The extras go before the names do: a Note is 15 tiles wide, so
    "WOHNZIMMER 21.5 45%" has to lose something, and dropping the humidity
    keeps the row readable where shortening the name to "WOHNZI" would not.

    Detail is chosen for the whole board rather than row by row, because a
    column of readings where one row happens to carry a humidity its neighbours
    dropped reads as a mistake — even though each row on its own was as full as
    it could be.
    """
    names = [to_board_text(reading.name, umlauts) for reading in readings]
    levels = [(secondary, trend), (secondary, False), ("none", False)]
    values: list[str] = []
    for level_secondary, level_trend in levels:
        values = [
            row_value(reading, units, decimals, sign, secondary=level_secondary, trend=level_trend)
            for reading in readings
        ]
        if all(not value or len(name) + 1 + len(value) <= width for name, value in zip(names, values, strict=True)):
            break
    return [_compose(name, value, width) for name, value in zip(names, values, strict=True)]


def module_row(
    reading: Reading,
    width: int,
    units: str,
    decimals: int,
    sign: str,
    *,
    secondary: str,
    trend: bool,
    umlauts: str = "expand",
) -> str:
    """One module's row, sized on its own."""
    return board_rows([reading], width, units, decimals, sign, secondary=secondary, trend=trend, umlauts=umlauts)[0]


def layout(rows: list[str], width: int, height: int) -> list[str]:
    """Pad *rows* out to a full board, dropping whatever does not fit.

    Modules are already in the user's chosen order, so the ones that fall off a
    Note are the ones ranked last — that is the ordering control doing its job.
    """
    board = [row[:width].ljust(width) for row in rows[:height]]
    board += [" " * width for _ in range(height - len(board))]
    return board


# --------------------------------------------------------------------------
# Rotating refresh tokens
# --------------------------------------------------------------------------


def _data_dir() -> Path:
    """FiestaBoard's writable data directory, where ``config.json`` lives.

    Derived the same way the core derives it, from the installed package rather
    than from a private attribute of the config manager. Falls back to a dot
    directory in the user's home when the plugin runs outside a FiestaBoard
    checkout (tests, a bare `python -c` import).
    """
    try:
        import src.config_manager as config_manager

        return Path(config_manager.__file__).resolve().parent.parent / "data"
    except Exception:  # noqa: BLE001 — any import problem means "not in a checkout"
        return Path.home() / ".fiestaboard"


def token_store_path() -> Path:
    """Where the rotated refresh token is cached.

    ``NETATMO_TOKEN_STORE`` overrides it, for installs whose data directory is
    not writable or that keep secrets on a separate volume.
    """
    override = os.getenv("NETATMO_TOKEN_STORE")
    if override:
        return Path(override)
    return _data_dir() / "netatmo_weather_tokens.json"


def token_key(client_id: str, seed_token: str) -> str:
    """Cache key for one credential pair.

    Keyed by the *seed* — the token pasted into the settings — so that pasting
    a fresh one starts a new chain instead of resuming a rotated token that
    Netatmo has already invalidated. That is exactly what a user does after the
    chain breaks, and it has to be the thing that fixes it.
    """
    digest = hashlib.sha256(f"{client_id}\n{seed_token}".encode()).hexdigest()
    return digest[:16]


def read_stored_token(key: str) -> str:
    """Return the cached refresh token for *key*, or "" when there is none."""
    path = token_store_path()
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return ""
    value = stored.get(key) if isinstance(stored, dict) else None
    return str(value) if value else ""


def write_stored_token(key: str, refresh_token: str) -> None:
    """Cache the rotated refresh token, replacing the file atomically.

    A half-written token file is a plugin that cannot authenticate again, so
    the write goes to a temporary file in the same directory and is renamed
    over the old one. Failures are logged, not raised: a read-only data
    directory costs the user a re-paste after a restart, which is better than
    breaking the render that just succeeded.
    """
    path = token_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stored = json.loads(path.read_text())
            if not isinstance(stored, dict):
                stored = {}
        except (OSError, ValueError):
            stored = {}
        stored[key] = refresh_token

        # Same directory, so the rename that follows is atomic.
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(stored))
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as error:
        logger.warning("Could not cache the rotated Netatmo refresh token: %s", error)


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------


class NetatmoWeatherPlugin(PluginBase):
    """Netatmo Weather Station readings, one module per board row."""

    def __init__(self, manifest: dict[str, Any]):
        super().__init__(manifest)
        self._access_token = ""
        self._access_expires_at = 0.0
        # fetch_data runs once per board in a thread pool, so two Flagship and
        # Note renders can land on an expired token at the same moment.
        self._token_lock = threading.Lock()

    @property
    def plugin_id(self) -> str:
        return "netatmo_weather"

    # -- configuration ----------------------------------------------------

    def credentials(self) -> tuple[str, str, str]:
        """Client id, client secret and seed refresh token.

        The settings form wins; the environment is the fallback for installs
        that keep secrets outside ``config.json``.
        """
        return (
            str(self.config.get("client_id") or os.getenv("NETATMO_CLIENT_ID", "")).strip(),
            str(self.config.get("client_secret") or os.getenv("NETATMO_CLIENT_SECRET", "")).strip(),
            str(self.config.get("refresh_token") or os.getenv("NETATMO_REFRESH_TOKEN", "")).strip(),
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Reject a half-filled credential set and a timezone that will not resolve.

        A completely empty credential set is *not* an error: it is what a fresh
        install looks like, and the plugin reports itself unavailable instead.
        Two out of three, on the other hand, only ever produces a confusing
        ``invalid_client`` from Netatmo an hour later.
        """
        errors: list[str] = []

        fields = ("client_id", "client_secret", "refresh_token")
        filled = [name for name in fields if str(config.get(name) or "").strip()]
        if filled and len(filled) < len(fields):
            missing = ", ".join(name for name in fields if name not in filled)
            errors.append(f"Netatmo credentials are incomplete — also needed: {missing}")

        timezone = config.get("timezone", "")
        if isinstance(timezone, str) and timezone.strip():
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                errors.append(f"Invalid timezone: {timezone}")
        elif timezone is not None and not isinstance(timezone, str):
            errors.append(f"Invalid timezone: {timezone!r}")

        modules = config.get("modules")
        if modules is not None and not isinstance(modules, list):
            errors.append("Modules must be a list of Netatmo module ids")

        return errors

    def _timezone(self) -> ZoneInfo:
        """Configured timezone, falling back to the FiestaBoard-wide setting."""
        timezone = self.config.get("timezone")
        if not timezone:
            try:
                from src.config import Config

                timezone = Config.GENERAL_TIMEZONE
            except Exception:  # noqa: BLE001 — outside a FiestaBoard install
                timezone = ""
        try:
            return ZoneInfo(timezone or DEFAULT_TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return ZoneInfo(DEFAULT_TIMEZONE)

    # -- Netatmo API ------------------------------------------------------

    def _renew_access_token(self, timeout: int) -> str:
        """Exchange a refresh token for an access token, following the rotation.

        Netatmo hands back a new refresh token on every call and retires the
        one just used. The new one is cached; if the cached chain has gone
        stale — another client refreshed it, the cache file was restored from a
        backup — the seed from the settings is tried once before giving up.
        """
        client_id, client_secret, seed = self.credentials()
        key = token_key(client_id, seed)

        attempts = [read_stored_token(key) or seed]
        if attempts[0] != seed:
            attempts.append(seed)

        last_error = "No refresh token configured"
        for refresh_token in attempts:
            try:
                response = requests.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=timeout,
                )
            except requests.RequestException as error:
                raise NetatmoError(f"Netatmo unreachable: {error}") from error

            if response.status_code == 200:
                payload = response.json()
                rotated = str(payload.get("refresh_token") or "")
                if rotated and rotated != refresh_token:
                    write_stored_token(key, rotated)
                self._access_token = str(payload.get("access_token") or "")
                # expires_in is the documented field; expire_in appears in older
                # responses. Three hours is Netatmo's actual lifetime.
                lifetime = int(payload.get("expires_in") or payload.get("expire_in") or 10800)
                self._access_expires_at = time.time() + max(lifetime - TOKEN_EXPIRY_MARGIN, 0)
                return self._access_token

            last_error = _error_message(response)
            logger.debug("Netatmo token refresh rejected: %s", last_error)

        self._access_token = ""
        self._access_expires_at = 0.0
        raise NetatmoError(f"Netatmo rejected the refresh token: {last_error}")

    def _token(self, timeout: int = HTTP_TIMEOUT) -> str:
        """A usable access token, renewed only when the cached one is stale."""
        with self._token_lock:
            if self._access_token and time.time() < self._access_expires_at:
                return self._access_token
            return self._renew_access_token(timeout)

    def _invalidate_token(self) -> None:
        """Forget the access token so the next call renews it."""
        with self._token_lock:
            self._access_token = ""
            self._access_expires_at = 0.0

    def station_body(self, timeout: int = HTTP_TIMEOUT) -> dict[str, Any]:
        """Fetch ``getstationsdata`` and return its ``body``.

        A 401/403 is retried once with a fresh access token: Netatmo can revoke
        a token before its stated lifetime is up (app credentials changed,
        password reset), and the retry turns a day of blank boards into one
        extra request.
        """
        response = self._get_station(self._token(timeout), timeout)
        if response.status_code in (401, 403):
            self._invalidate_token()
            response = self._get_station(self._token(timeout), timeout)
        if response.status_code != 200:
            raise NetatmoError(_error_message(response))
        return response.json().get("body") or {}

    @staticmethod
    def _get_station(token: str, timeout: int) -> requests.Response:
        """One ``getstationsdata`` call. Favourites belong to other people's homes."""
        try:
            return requests.get(
                STATION_URL,
                params={"get_favorites": "false"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
        except requests.RequestException as error:
            raise NetatmoError(f"Netatmo unreachable: {error}") from error

    # -- settings options -------------------------------------------------

    def get_options(self, request: OptionsRequest) -> OptionsResult | list[Option]:
        """List the account's modules so the user can pick and order them.

        Runs while the settings dialog is open, on a throwaway instance with
        the draft credentials applied — so it must survive being called before
        anything is configured.

        The plugin guide asks options browsing not to mutate persisted state,
        and this method observes that with one deliberate exception: if the
        request needs a new access token, the refresh token Netatmo rotates in
        return is written to the token cache. Discarding it would leave the
        cache holding a token Netatmo has just retired, so *not* writing is the
        destructive option — it would break the next render rather than protect
        it. Nothing else here touches stored state.
        """
        if request.options_id != "modules":
            raise NotImplementedError(request.options_id)

        client_id, client_secret, refresh_token = self.credentials()
        if not (client_id and client_secret and refresh_token):
            raise OptionsUnavailable("Add your Netatmo client id, secret and refresh token first")

        try:
            readings = parse_stations(self.station_body(OPTIONS_TIMEOUT))
        except NetatmoError as error:
            raise OptionsUnavailable(str(error)) from error

        options: list[Option] = []
        for reading in readings:
            # The current value goes in the description: a station of four
            # NAModule4s is otherwise four identical-looking rows.
            preview = row_value(reading, DEFAULT_UNITS, 1, DEGREE, secondary="none", trend=False)
            options.append(
                Option(
                    value=reading.module_id,
                    label=reading.name,
                    description=f"{reading.kind.title()} — {preview}" if preview else reading.kind.title(),
                    group=reading.station,
                    preview=to_board_text(f"{reading.name} {preview}".strip()),
                )
            )
        return options

    # -- rendering --------------------------------------------------------

    def fetch_data(self) -> PluginResult:
        """Read the station and lay its modules out for this board."""
        client_id, client_secret, refresh_token = self.credentials()
        if not (client_id and client_secret and refresh_token):
            return PluginResult(available=False, error="Netatmo credentials are not configured")

        try:
            readings = apply_custom_names(
                parse_stations(self.station_body()),
                self.config.get("module_names") or {},
            )
        except NetatmoError as error:
            logger.warning("Netatmo fetch failed: %s", error)
            return PluginResult(available=False, error=str(error))
        except Exception as error:  # noqa: BLE001 — a bad payload must not break the render loop
            logger.exception("Unexpected error reading the Netatmo station")
            return PluginResult(available=False, error=str(error))

        if not readings:
            return PluginResult(available=False, error="The Netatmo account has no weather station")

        return PluginResult(
            available=True,
            data=self._build_data(readings),
            formatted_lines=self._build_lines(readings),
        )

    def _options(self) -> tuple[str, int, str, bool, str]:
        """Display settings, each clamped to what the code actually handles."""
        units = self.config.get("units", DEFAULT_UNITS)
        units = units if units in UNITS else DEFAULT_UNITS
        decimals = 0 if self.config.get("decimals") == 0 else 1
        secondary = self.config.get("secondary", DEFAULT_SECONDARY)
        secondary = secondary if secondary in SECONDARY_CHOICES else DEFAULT_SECONDARY
        trend = bool(self.config.get("show_trend", False))
        umlauts = "strip" if self.config.get("umlauts") == "strip" else "expand"
        return units, decimals, secondary, trend, umlauts

    def _sign(self) -> str:
        """Degree sign for the board being rendered on."""
        degree = self.config.get("degree_sign", DEFAULT_DEGREE)
        degree = degree if degree in DEGREE_CHOICES else DEFAULT_DEGREE
        board = self.board
        return degree_sign(degree, board.device_type if board else "flagship")

    def _build_lines(self, readings: list[Reading]) -> list[str]:
        """One row per selected module, laid out for this board."""
        units, decimals, secondary, trend, umlauts = self._options()
        board = self.board
        width = board.width if board else FALLBACK_WIDTH
        height = board.height if board else FALLBACK_HEIGHT
        sign = self._sign()

        rows = board_rows(
            select_readings(readings, self.config.get("modules")),
            width,
            units,
            decimals,
            sign,
            secondary=secondary,
            trend=trend,
            umlauts=umlauts,
        )
        return layout(rows, width, height)

    def _build_data(self, readings: list[Reading]) -> dict[str, Any]:
        """Template variables: the named highlights, plus the laid-out board."""
        units, decimals, secondary, trend, umlauts = self._options()
        sign = self._sign()
        selected = select_readings(readings, self.config.get("modules"))
        lines = self._build_lines(readings)

        indoor = _first(readings, lambda r: r.kind == "indoor" and r.temperature is not None)
        main = _first(readings, lambda r: r.is_main) or indoor
        outdoor = _first(readings, lambda r: r.kind == "outdoor")
        rain = _first(readings, lambda r: r.kind == "rain")
        wind = _first(readings, lambda r: r.kind == "wind")

        newest = max((r.measured_at or 0 for r in readings), default=0)
        zone = self._timezone()

        data: dict[str, Any] = {
            "station": to_board_text(readings[0].station, umlauts),
            "updated": datetime.fromtimestamp(newest, zone).strftime("%H:%M") if newest else "",
            "age": str(max(int((time.time() - newest) // 60), 0)) if newest else "",
            "module_count": str(len(selected)),
            "temp_unit": temperature_unit(units, sign),
            "indoor_name": to_board_text(indoor.name, umlauts) if indoor else "",
            "indoor_temp": number(convert_temperature(indoor.temperature, units), decimals) if indoor else "",
            "indoor_humidity": _integer(indoor.humidity if indoor else None),
            "indoor_co2": _integer(indoor.co2 if indoor else None),
            "indoor_noise": _integer(main.noise if main else None),
            "indoor_trend": TREND_MARKS.get(indoor.temp_trend, "") if indoor else "",
            "pressure": number(convert_pressure(main.pressure if main else None, units), 0 if units == "metric" else 2),
            "pressure_trend": TREND_MARKS.get(main.pressure_trend, "") if main else "",
            "outdoor_name": to_board_text(outdoor.name, umlauts) if outdoor else "",
            "outdoor_temp": number(convert_temperature(outdoor.temperature, units), decimals) if outdoor else "",
            "outdoor_humidity": _integer(outdoor.humidity if outdoor else None),
            "outdoor_min": number(convert_temperature(outdoor.min_temp, units), decimals) if outdoor else "",
            "outdoor_max": number(convert_temperature(outdoor.max_temp, units), decimals) if outdoor else "",
            "outdoor_trend": TREND_MARKS.get(outdoor.temp_trend, "") if outdoor else "",
            "rain_hour": number(convert_rain(rain.rain_hour, units), 1) if rain else "",
            "rain_today": number(convert_rain(rain.rain_today, units), 1) if rain else "",
            "wind": number(convert_wind(wind.wind, units), 0) if wind else "",
            "wind_gust": number(convert_wind(wind.gust, units), 0) if wind else "",
            "wind_from": compass(wind.wind_angle) if wind else "",
        }

        # One variable that fills the whole board. FiestaBoard's template engine
        # splits a value on "\n" across board rows, so `{{netatmo_weather.block}}` on the
        # first template line reproduces the layout without needing |wrap.
        data["block"] = "\n".join(lines)
        # line1..line6 mirror the same rows individually. They keep their full
        # board width on purpose: the engine pads *around* what it is given, so
        # an rstripped row would be indented twice on a centered line.
        for index in range(FALLBACK_HEIGHT):
            data[f"line{index + 1}"] = lines[index] if index < len(lines) else ""

        data["modules"] = [
            {
                "name": to_board_text(reading.name, umlauts),
                "temp": number(convert_temperature(reading.temperature, units), decimals),
                "humidity": _integer(reading.humidity),
                "co2": _integer(reading.co2),
                "battery": _integer(reading.battery),
                "trend": TREND_MARKS.get(reading.temp_trend, ""),
                "line": module_row(
                    reading,
                    FALLBACK_WIDTH,
                    units,
                    decimals,
                    sign,
                    secondary=secondary,
                    trend=trend,
                    umlauts=umlauts,
                ).rstrip(),
            }
            for reading in selected
        ]

        return data


def _first(readings: list[Reading], predicate: Any) -> Reading | None:
    """First reading matching *predicate*, or ``None``."""
    return next((reading for reading in readings if predicate(reading)), None)


def _integer(value: float | None) -> str:
    """Whole-number measurement as board text, empty when unreported."""
    return "" if value is None else str(int(value))


def _error_message(response: requests.Response) -> str:
    """Human-readable reason from a Netatmo error response.

    Netatmo uses two shapes: OAuth errors are ``{"error": "invalid_grant"}``,
    API errors are ``{"error": {"code": 3, "message": "..."}}``.
    """
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or f"HTTP {response.status_code}")
    if error:
        description = payload.get("error_description")
        return f"{error}{f' ({description})' if description else ''}"
    return f"HTTP {response.status_code}"


# Export the plugin class
Plugin = NetatmoWeatherPlugin
