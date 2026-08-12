# Changelog

All notable changes to this plugin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version here is the source of truth for `manifest.json` — a test fails if
the two drift apart.

## [Unreleased]

## [1.2.1] — 2026-08-12

Documentation only — no change to the variables, the settings or what the
board shows.

### Added

- A **color-per-room sample page** in the README, with the board it produces:
  the inline `COLOR(IF(...))` formula, the rendered result, and the three
  things that break it — the `{{=` prefix, the tile the color marker costs,
  and modules without a CO2 sensor falling through the last `IF` branch.
- `docs/board-co2-colors.png`, rendered from what the template engine actually
  returns for that page.
- What an **empty rain or wind variable means**: the Weather Station is
  modular, those gauges are separate purchases, and the plugin reports nothing
  rather than `0` — a station without a rain gauge does not know that it is not
  raining. Includes the `DEFAULT()` and `IF(ISBLANK(...))` fallbacks that keep
  a page tidy.

### Changed

- The **Degree sign** setting no longer promises a heart on a Note. FiestaBoard
  sends character 62 to every board type; a Flagship prints a degree sign, and
  a Note prints whatever it carries at that position — a heart on some, an
  empty flap on others. `Auto`, the default, drops the sign on note-shaped
  boards either way, which is why it is the default.

## [1.2.0] — 2026-08-12

### Removed

- **`module1_*` … `module6_*`**, one release after they arrived. They existed
  only to give colour rules a top-level field to attach to, and a template can
  do that itself with an inline formula — with thresholds the plugin has no
  business guessing:

  ```text
  {{= COLOR(IF(netatmo_weather.modules.1.co2 >= 1000, "red",
            IF(netatmo_weather.modules.1.co2 >= 800, "yellow", "green"))) }}{{netatmo_weather.modules.1.co2}} PPM
  ```

  Twenty-four variables and twelve colour-rule entries were a poor trade for
  something the template language already does, and every one of them was
  another row in the editor's variable list. The `modules` array is unchanged
  and remains the way to read a single module.
- The colour rules that came with them. `indoor_co2` and `outdoor_temp` keep
  theirs.

## [1.1.0] — 2026-08-12

### Added

- **`module1_*` … `module6_*`** — `name`, `temp`, `humidity` and `co2` for the
  Nth module in your order, as flat variables alongside the `modules` array.
  They exist because colour rules cannot reach into an array: FiestaBoard's
  template engine reads only the first two segments of an expression, so
  `{{netatmo_weather.modules.1.co2}}` resolves its colour against the field
  `modules` — the whole list — and never matches a threshold.
  `{{netatmo_weather.module2_co2}}` is a top-level field, so it does.
- Colour rules for every `moduleN_co2`, with the same air-quality thresholds
  as `indoor_co2`, and for every `moduleN_temp` with **no** defaults: an indoor
  module and the one hanging outside want different thresholds, and the plugin
  cannot tell which slot is which. The field still appears in the colour-rules
  UI, ready for your own per-board thresholds.
- Board screenshots — `docs/board-display.png` (Flagship, the hero image),
  `docs/board-note.png` and `docs/board-sample-page.png` — plus the manifest's
  `screenshots` array, which the registry catalog reads. All three are renders
  of what the plugin actually returns for a sample station, not mock-ups.

### Changed

- `docs/SETUP.md` follows the four-step *Quick Setup* order from FiestaBoard's
  plugin development guide, and nests *Environment Variables* under
  *Configuration Reference* as the guide's template does.

## [1.0.0] — 2026-08-12

Initial release.

### Added

- One board row per Netatmo module — name on the left, reading on the right —
  laid out for the board it renders on, so the same setup fits a Flagship
  (22x6) and a Note (15x3).
- A module picker fed from the account itself (`remote-options`), with drag
  ordering and an optional short name per module, because "Wohnzimmer
  Erdgeschoss" is 23 tiles before a single degree is shown.
- Named variables for the highlights (`indoor_temp`, `outdoor_temp`,
  `indoor_co2`, `rain_today`, `wind`, …), a `modules` array, and `block` /
  `line1`…`line6` for the laid-out board.
- Metric and imperial units. Netatmo always reports metric; the conversion is
  the plugin's.
- Default color rules for CO2 and outdoor temperature.
- Credentials from the settings form or from `NETATMO_CLIENT_ID`,
  `NETATMO_CLIENT_SECRET` and `NETATMO_REFRESH_TOKEN`.

### Notes

- **Rotating refresh tokens.** Netatmo retires a refresh token the moment it is
  used, and FiestaBoard gives plugins no way to write their own config back, so
  the rotated token is cached in `netatmo_weather_tokens.json` in FiestaBoard's data
  directory (override with `NETATMO_TOKEN_STORE`). The token pasted into the
  settings stays the seed of that chain and is never overwritten — re-pasting
  it starts a fresh chain, which is how a broken chain is repaired.
- **The degree sign is board-dependent.** Tile 62 prints `°` on a Flagship and
  a heart on a Note. The default drops it on note-shaped boards.
