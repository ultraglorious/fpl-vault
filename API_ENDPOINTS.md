# FPL API Endpoints

Base URL: `https://fantasy.premierleague.com/api/`

All endpoints are undocumented and community-discovered. All use `GET`.

## Reference data

### `bootstrap-static/`
Returns all core reference data in one call: players (elements), teams, positions (element_types), gameweeks (events), chips, phases, game settings, and element stats. The single most important endpoint.

### `event-status/`
Returns current gameweek status: which gameweeks are active/finished, when bonus points were added, etc.

## Fixtures and gameweeks

### `fixtures/`
Returns every fixture of the season as a JSON array. Each fixture includes teams, gameweek, kickoff time, difficulty ratings, and the final score (if played).

Query params:
- `?event={gw}` — fixtures for a specific gameweek

### `event/{event_id}/live/`
Returns per-player stats and points breakdown for a specific gameweek. Includes the `explain` array showing how each player's points were calculated.

### `dream-team/{event_id}/`
Returns the best XI for a given gameweek. `top_player` is the highest scorer.

## Player data

### `element-summary/{element_id}/`
Returns a player's full profile across three sections:
- `fixtures` — upcoming fixtures with difficulty ratings
- `history` — per-gameweek stats for the current season
- `history_past` — season-level stats for previous seasons

## Team data

### `team/set-piece-notes/`
Returns set-piece taker information per team (corners, free kicks, penalties).

## Manager data (requires specific IDs)

### `entry/{manager_id}/`
Returns a manager's basic info and classic leagues joined.

### `entry/{manager_id}/history/`
Returns a manager's performance: current season gameweeks (`current`), previous seasons (`past`), and chips used (`chips`).

### `entry/{manager_id}/event/{event_id}/picks/`
Returns a manager's picks, active chip, automatic subs, and points for a specific gameweek.

### `leagues-classic/{league_id}/standings`
Returns classic league standings. Supports `?page_standings={n}` for pagination.

## Authentication-required

These endpoints require login and are not currently usable:

### `me/`
Returns profile info for the authenticated user, including their `entry` (manager) ID.

### `my-team/{manager_id}/`
Returns team state: current picks, chips available, transfers.
