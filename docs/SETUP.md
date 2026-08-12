# Netatmo Weather Setup Guide

Show the readings of your Netatmo Weather Station on your Vestaboard — room temperature, CO2, humidity, outdoor conditions, rain and wind.

## Overview

**What it does.** Every module of your station becomes a board row: the room name on the left, its reading on the right. On a Flagship six modules fit with humidity next to each temperature; on a Note three fit, and the plugin drops the humidity rather than cutting the room names.

**Prerequisites.**

- A Netatmo Weather Station on your Netatmo account
- A free developer app at [dev.netatmo.com](https://dev.netatmo.com), which gives you a client id, a client secret and a refresh token

The plugin reads the Weather Station only. Thermostats, valves, cameras and the Healthy Home Coach live on the same account but a different endpoint.

## Quick Setup

### 1. Enable the Plugin

1. **Integrations → Install from repository:**

   ```text
   https://github.com/rummeyer/fiestaboard-plugin--netatmo-weather
   ```

2. Open the **Netatmo Weather** card and switch **Enabled** on. It reports itself unavailable until it has credentials — that is the next step.

### 2. Configure Netatmo Weather

**Create a Netatmo app.**

1. Sign in at [dev.netatmo.com](https://dev.netatmo.com) with the account that **owns the station** — not a shared or guest account.
2. Open **My apps** → **Create**, give it a name (`FiestaBoard`), a one-line description, accept the terms and save.
3. The app page now shows a **Client ID** and a **Client Secret**. Keep the tab open.

**Generate a refresh token.** On the same app page, scroll to **Token generator**:

1. Tick the **`read_station`** scope. Nothing else is needed — the plugin never writes.
2. Click **Generate Token** and approve the request.
3. Copy the **refresh token**. The access token next to it is short-lived and is *not* what the plugin wants.

The refresh token belongs to this app and this account. Revoking the app in your Netatmo account invalidates it, and so does deleting the app.

**Fill in the plugin settings.**

1. Paste **Client ID**, **Client Secret** and **Refresh Token** into the Netatmo Weather card.
2. Open **Modules**. The picker asks Netatmo for your station and lists every module it finds, with its current reading next to the name. Pick the ones you want, drag them into the order they should appear, and give any long-named module a short board name.

   Leaving the picker empty shows every module the station has, in Netatmo's own order.

### 3. Create a Board Template

Create a page of type *Plugin* and pick **Netatmo Weather**. The plugin fills the board itself, so no template is needed.

For a page of your own, put `block` on the first line and leave the rest empty:

```text
{{netatmo_weather.block}}
```

Or compose the rows yourself — with the living room and the outdoor module selected, in that order:

```text
{{netatmo_weather.station}} {{netatmo_weather.updated}}

{{netatmo_weather.line1}}
{{netatmo_weather.line2}}
CO2 {{netatmo_weather.indoor_co2}} PPM
REGEN {{netatmo_weather.rain_today}} MM
```

![Sample page with a green CO2 reading](./board-sample-page.png)

The green tile in front of the CO2 reading comes from the plugin's default color rules — nothing in the template asks for it.

### 4. View on Your Board

The board updates on your page's normal schedule. The station itself measures every five minutes, so a refresh interval of five to ten minutes is as fresh as the data ever gets.

![Netatmo Weather on a Flagship](./board-display.png)

On a Note the same station re-wraps to three rows, and the humidity is dropped rather than the room names:

![Netatmo Weather on a Note](./board-note.png)

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{netatmo_weather.station}}` | Station name | `ZUHAUSE` |
| `{{netatmo_weather.updated}}` | Local time of the most recent measurement | `14:35` |
| `{{netatmo_weather.age}}` | Minutes since that measurement | `6` |
| `{{netatmo_weather.module_count}}` | How many modules the layout is showing | `4` |
| `{{netatmo_weather.temp_unit}}` | Unit suffix, board-aware | `°C` |
| `{{netatmo_weather.indoor_name}}` | Name of the indoor module in use | `WOHNZIMMER` |
| `{{netatmo_weather.indoor_temp}}` | Indoor temperature, no unit suffix | `21.5` |
| `{{netatmo_weather.indoor_humidity}}` | Indoor humidity in percent | `45` |
| `{{netatmo_weather.indoor_co2}}` | Indoor CO2 in ppm | `612` |
| `{{netatmo_weather.indoor_noise}}` | Noise in dB, main unit only | `37` |
| `{{netatmo_weather.indoor_trend}}` | `+` rising, `-` falling, `=` stable | `+` |
| `{{netatmo_weather.pressure}}` | Air pressure in mbar or inHg | `1017` |
| `{{netatmo_weather.pressure_trend}}` | Pressure trend | `-` |
| `{{netatmo_weather.outdoor_name}}` | Name of the outdoor module | `DRAUSSEN` |
| `{{netatmo_weather.outdoor_temp}}` | Outdoor temperature | `8.4` |
| `{{netatmo_weather.outdoor_humidity}}` | Outdoor humidity | `78` |
| `{{netatmo_weather.outdoor_min}}` / `{{netatmo_weather.outdoor_max}}` | Today's low and high | `3.6` / `11.2` |
| `{{netatmo_weather.outdoor_trend}}` | Outdoor trend | `-` |
| `{{netatmo_weather.rain_hour}}` / `{{netatmo_weather.rain_today}}` | Rain in the last hour / 24 hours | `0.4` / `6.9` |
| `{{netatmo_weather.wind}}` / `{{netatmo_weather.wind_gust}}` / `{{netatmo_weather.wind_from}}` | Wind speed, gust, direction | `12` / `27` / `SW` |
| `{{netatmo_weather.block}}` | The whole laid-out board, rows separated by newlines | see above |
| `{{netatmo_weather.line1}}` … `{{netatmo_weather.line6}}` | The same rows individually | `WOHNZIMMER   21.5° 45%` |
| `{{netatmo_weather.modules.0.name}}` | Per-module array: `name`, `temp`, `humidity`, `co2`, `battery`, `trend`, `line` | `WOHNZIMMER` |

A measurement your station cannot make comes out empty rather than as a zero — no rain gauge means a blank `rain_today`, not `0`.

## Configuration Reference

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `enabled` | boolean | `false` | Enable the plugin |
| `client_id` | string | *(empty)* | From your app at dev.netatmo.com |
| `client_secret` | string | *(empty)* | From the same app |
| `refresh_token` | string | *(empty)* | Token generator, `read_station` scope |
| `modules` | array | *(empty)* | Which modules to show, in order. Empty means all |
| `module_names` | object | `{}` | Short board name per module |
| `units` | `metric` \| `imperial` | `metric` | °C/mm/km/h vs. °F/in/mph |
| `decimals` | `0` \| `1` | `1` | `21` or `21.5` |
| `secondary` | `none` \| `humidity` \| `co2` \| `battery` | `humidity` | Second reading after the temperature |
| `show_trend` | boolean | `false` | Add `+` / `-` / `=` |
| `degree_sign` | `auto` \| `on` \| `off` | `auto` | See *The degree sign* below |
| `umlauts` | `expand` \| `strip` | `expand` | `KUECHE` vs. `KUCHE` |
| `timezone` | string | *(empty)* | IANA name for `updated`. Empty follows the FiestaBoard-wide timezone |
| `refresh_seconds` | integer | `600` | Poll interval, floored at 300 seconds |

### The degree sign

Vestaboard character 62 is a degree sign on a Flagship and a heart on a Note — one tile, two prints. `auto` shows `°` on a Flagship and leaves it off on note-shaped boards, so a Note reads `WOHNZIMMER 21.5` rather than `WOHNZIMMER 21.5❤`. Set it to **Always** if you would rather have the heart than no unit.

### Rate limits

Netatmo allows 500 calls per hour per user. The plugin makes one call per refresh plus the occasional token renewal, so the default ten-minute interval uses about seven an hour. The floor is five minutes because that is how often the station measures.

### Environment Variables

All optional — the settings form is the normal way in. These exist for installs that keep secrets outside `config.json`:

```bash
NETATMO_CLIENT_ID=...
NETATMO_CLIENT_SECRET=...
NETATMO_REFRESH_TOKEN=...

# Where the rotated refresh token is cached.
# Defaults to netatmo_weather_tokens.json in FiestaBoard's data directory.
NETATMO_TOKEN_STORE=/data/netatmo_weather_tokens.json
```

**Why there is a token cache.** Netatmo retires a refresh token the moment it is used and hands back a new one. FiestaBoard gives plugins no way to write their own settings back, so the plugin caches the current token in `netatmo_weather_tokens.json` next to FiestaBoard's `config.json`, with owner-only permissions. The token you pasted into the settings stays the seed of that chain and is never overwritten — which is what makes re-pasting it a repair.

## Troubleshooting

**"Netatmo credentials are not configured".** One of the three fields is empty. The plugin needs all of client id, client secret and refresh token; two out of three is refused when you save.

**"Netatmo rejected the refresh token".** The chain is broken — the app was deleted or revoked, the token was generated for a different app, or another program is refreshing the same app's token in parallel. Generate a fresh refresh token in the token generator and paste it in. Pasting a new one starts a new chain, so this is the fix rather than a workaround.

**The board is blank after a container restart.** If the data directory is read-only, the rotated token cannot be cached and the plugin falls back to the token in your settings — which Netatmo has already retired. Check the container log for *"Could not cache the rotated Netatmo refresh token"* and either make the data directory writable or point `NETATMO_TOKEN_STORE` somewhere that is.

**A module is missing from the board.** More modules were selected than the board has rows. The order in the picker decides who survives — drag the important ones to the top.

**A room shows only its name.** That module has not reported since the station last synced — a flat battery, or out of radio range of the main unit. The row keeps the name on purpose, so you can see which sensor went quiet.

**Temperatures are the wrong scale.** Netatmo's API always reports Celsius, whatever your Netatmo account displays. Switch **Units** to Imperial in the plugin, not in the Netatmo app.

**The measurement time is off by hours.** Check **Timezone**. Empty means the FiestaBoard-wide timezone (Settings → General), which may differ from where the board hangs.

**Room names look mangled.** The board has no umlaut tiles. **Expand** writes `KUECHE`, **Strip** writes `KUCHE`. For anything else — accents, emoji in a room name — the character is dropped; give the module a short board name in the picker instead.

**"User usage reached".** Netatmo's hourly quota, usually because something else on the same account is polling hard. Raise **Refresh Interval**.
