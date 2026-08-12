# Changelog

All notable changes to this plugin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version here is the source of truth for `manifest.json` — a test fails if
the two drift apart.

## [Unreleased]

### Missing

- `docs/board-display.png` and the manifest's `screenshots` array. The plugin
  directory falls back to the literal `previews` until a photograph of a real
  board exists; the registry checklist wants a hero image before submission.

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
