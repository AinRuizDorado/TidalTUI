# AGENTS.md

## Project overview

**tidal-tui** (package name in this repo: `tidaltui`, git-tracked path historically `lowtide`) is a keyboard-first terminal UI client for TIDAL built with [Textual](https://textual.textualize.io) (`>= 8.2.3`) + [tidalapi](https://github.com/tamland/python-tidal) + mpv. It plays music through an `mpv --idle` subprocess driven over a Unix-socket JSON IPC, renders album art with the kitty graphics protocol (`textual-image`), and integrates with Last.fm (scrobbling + recommendations), MPRIS (media keys), and matugen (Material You theming).

**Linux-only** (Unix sockets, D-Bus, kitty graphics). Python 3.11+; repo uses `from __future__ import annotations`, PEP 604 unions, builtin generics, walrus, dataclasses, `IntEnum`. No `match` statements. No tests suite, no linter config in the repo — verification is done by importing/compiling and, where possible, headless Textual `run_test()` pilots (see "Testing" below).

## How to run

```bash
.venv/bin/python -m tidaltui.main          # or: pip install -e . && tidal-tui
```

`main.py` → `TidalClient` (OAuth device-code login, tokens in `~/.config/tidal-tui/session.json`) → `TidalTUIApp(client).run()`. Subcommand: `tidal-tui import-spotify <dir>` imports Spotify GDPR history into the play-count store.

Config: `~/.config/tidal-tui/config.json` (`quality`, `music_dir`, `replaygain`, `alsa_*`, `crossfade`, `eq_theme`, `eq_labels`, `lastfm`, `matugen_colors_file`). See README.md for details.

## Git workflow

- After completing a task, commit the changes with an explanatory message describing **what** changed and **why** (concise, imperative style consistent with the repo's history).
- If the user requests a new feature, create the branch `feature/request` for it, commit the work there, and push it to the remote. Don't merge it yourself unless asked.

## Architecture

The app is a single Textual `App` with a fixed three-panel `Horizontal` layout plus a docked bottom bar:

```
TidalTUIApp (tidaltui/app.py)
├── Sidebar (ListView of nav items, id="nav")   — j/k handled locally
├── ContentArea (id="content-panel")            — hosts a *stack* of screen widgets
│     └── stack: [LibraryScreen, SearchScreen, ...]  (only stack[-1] visible)
├── QueuePanel (id="queue-panel")               — hidden by default
└── NowPlayingBar                               — track info, progress, EQ, lyrics
```

- **Screen navigation**: `ContentArea.push(widget)` / `pop()` / `replace(widget)` manage a widget stack (`_stack`); `TidalTUIApp.push_view`, `action_go_back` (escape), `_switch_root` (sidebar nav) wrap them. `open_object(obj)` routes any tidalapi object (album/artist/playlist/mix) by inspecting `type(obj).__name__` to the matching screen.
- **Screens** (`tidaltui/screens/`) are plain `Widget`s (NOT `Screen` subclass), each with a shared pattern: `@work(thread=True)` loader → `app.client.*` fetch → `call_from_thread(self._populate, ...)` → fill `ListView`/`TrackList`. Tabbed screens use `TabbedContent`/`TabPane`.
- **Tabbed screens**: `library.py`, `search.py`, `artist.py`, `favorites.py`, `new_for_you.py`. The **Bio** tab in `artist.py` uses a `VerticalScroll` + `Static`.

## Key handling (IMPORTANT — vim-like, centralized)

The design goal: `hjkl` behave vim-like **everywhere**, regardless of focus:

- `TidalTUIApp.on_key` (app.py) — **App-level** handler. Only watches `h`/`l`: if the visible screen contains a `TabbedContent`, it calls `ContentArea.cycle_tabs(forward=...)` and stops the event. It fires even when focus is on the Sidebar/Queue (their widgets never stop `h`/`l`). It must NOT fire while typing in the search `Input` — safe because Textual's `Input` stops printable keys in `_on_key` before the event ever bubbles.
- `ContentArea.on_key` (app.py) — handles `j`/`k` by routing to `self.screen.focused`'s `action_cursor_down`/`action_cursor_up`; if the focused widget lacks those (e.g. a `VerticalScroll`), falls back to `action_scroll_down`/`action_scroll_up`, swallowing `textual.actions.SkipAction` (raised when content can't scroll) without stopping the event.
- `ContentArea.cycle_tabs(forward)` — looks at **`self._stack[-1]`** (the visible screen only — hidden screens beneath must never be cycled), lists its `TabPane`s, wraps around, sets `tabs.active = nxt.id`, then focuses the first focusable descendant of the new pane so `j`/`k`/Enter work immediately. Returns `True` if a tab switch happened.
- Local handlers: `Sidebar.on_key` (j/k on the nav), `QueuePanel.on_key` (j/k), `TrackList.on_key` (j/k, `a` = add to queue, `R` = radio).
- `h`/`l` on non-tabbed screens (album, playlist, radio, genre, journey, local) intentionally do nothing.

**Regression trap**: key events bubble only through the *focused widget's ancestors*. A handler on `ContentArea` is invisible when focus sits on the Sidebar — hence `h`/`l` live at the App level, not in individual screens. Keep it that way. Adding per-screen `on_key` for `h`/`l` (as `LibraryScreen` used to have) is considered duplication — it was removed and centralized.

## Layout/theme

- App CSS is inline in `TidalTUIApp.CSS` + per-widget `DEFAULT_CSS` with transparent backgrounds (designed for terminal opacity).
- **matugen theming** (`matugen_theme.py`): at startup, picks the newest JSON palette from `~/.local/state/quickshell/generated`, `~/.cache/matugen/images`, `~/.config/matugen` (or `matugen_colors_file`), maps Material You colors to Textual theme vars, and registers a `matugen` theme; `app._poll_matugen_theme` hot-reloads it every 3 s when the palette file changes.

## Playback & queue

- `Player` (player.py) manages the mpv subprocess + JSON IPC (`/tmp/tidaltui-mpv.sock`). Requests are matched by `request_id` to futures; `on_track_start`/`on_track_end` callbacks fire on `file-loaded`/`end-file` events. mpv args include `--prefetch-playlist=yes`, `--gapless-audio=yes`, replaygain, and optional ALSA output.
- `TidalTUIApp.enqueue_and_play(tracks, start_index)` rotates the list so the selected track is index 0, applies the active shuffle mode (see below), sets `self._queue`, then `_load_queue` (a worker thread) resolves URLs one-by-one, playing the first and appending the rest (rate-limit-aware; sleeps 0.15–0.5 s between calls, aborts when `_queue_gen` changes). The queue panel shows `▶` on the current track; `a` appends, `A` appends all.
- Volume persists via `volume_store.py`; queue persists via `_save_queue`/`_restore_queue` (`queue.json`), showing placeholder `_SavedTrack` objects immediately while real objects are fetched in the background.
- Crossfade: 1 s poll fades volume out over the last `crossfade_secs` of a track; restored on `_on_mpv_track_start`.

## Shuffle modes (weighted)

Four modes toggled by `s` (constants in `play_count_store.py`): `SHUFFLE_OFF`, `SHUFFLE_RANDOM`, `SHUFFLE_FAVOURITE`, `SHUFFLE_DISCOVERY`. Favourite/discovery use `PlayCountStore.weighted_shuffle` (cumulative-sum roulette over weights `count+1` or `max_count-count+1`). Play counts come from scrobbles (`Scrobbler.on_scrobble`), Last.fm top-tracks sync at startup, and the `import-spotify` CLI. Weighted modes also handle end-of-queue wrapping in `_on_mpv_track_end` and `action_next_track`.

## Recommender / radio

`Recommender` (recommender.py) powers track radio (`R` on a track), Genre Radio (TIDAL genres or Last.fm tags), and **Ride the Tide** (no seed). Discovery mode (`D` dial, `DiscoveryMode` IntEnum: Essential/Balanced/Adventurous) tunes similarity thresholds and novelty weighting. Without Last.fm it degrades to play-count-based/fallback searches and returns a human-readable "nudge" string shown in the radio screen.

## Last.fm & journey

- `Scrobbler` scrobbles at `position >= min(0.5 * duration, 240)` (duration ≥ 30 s) — update happens inside the app's 1 s player poll.
- `ScrobbleStore` caches the Last.fm history incrementally (2-year initial fetch) at `scrobbles.json`, feeding the **Listening Journey** heatmap (`journey.py`): artist × (year, month) counts, top-25 artists, `░▒▓█` shading per-artist or global scale (`G`), Enter/R open the artist/radio.
- `app._sync_lastfm_counts` syncs play counts from Last.fm top tracks at startup.

## MPRIS

`MPRISService` (mpris.py) exposes `org.mpris.MediaPlayer2.tidal-tui` with dbus-next; player actions are thin wrappers scheduling app callbacks; properties are updated via hand-sent `PropertiesChanged` signals (dbus-next's `emit_properties_changed` is not used). Art URL embedded in `Metadata` as `mpris:artUrl`. macOS-only quirks: `--vo=null` + video-add of downloaded art to feed the Now Playing widget.

## Widgets

- `TrackList` (track_list.py): `DataTable` with `#/Title/Artist/Album/Time`; `load()` fills + focuses the table (which auto-activates the enclosing tab via Textual's `TabPane.Focused`); posts `TrackSelected`/`TrackAppendRequested`/`TrackRadioRequested` messages. Row keys are the track index strings.
- `NowPlayingBar`: ~10 `reactive` props with watchers; 5-line synced lyrics (2 context lines, `lyrics.py` LRC parser) visible by default (`y` toggles); EQ toggle; bar height is content-driven (`height: auto`, docked bottom) so track info, lyrics and the controls row always coexist without clipping.
- `EQVisualizer`: purely simulated 30 fps cava-style bars (BPM-driven bass beats, gravity fall, peak hold, monstrcat smoothing), 5 gradient themes (`eq_theme`).
- `AlbumArt`: downloads via `requests` (or PIL for `file://`), stale-URL guard, renders with `textual_image`.

## Conventions

- All modules: `from __future__ import annotations`; relative imports within package.
- Screens attach payloads to `ListItem` objects as ad-hoc attributes (`item._album`, `item._artist`, `item._playlist`, `item._mix`, `item._nav_key`, `item._queue_index`) and handle `on_list_view_selected`; `TrackList` payload comes via its messages.
- Async fetches: `@work(thread=True)` + `call_from_thread`; never touch widgets from worker threads.
- Notifications via `self.app.notify(...)`; error text goes to a status `Label` in the screen.
- Known private coupling: screens touch `app._ride_the_tide_cache`, `app.recommender`, `app._scrobble_store` — tolerated pattern, don't refactor casually.
- TIDAL API: all access goes through `TidalClient` (`tidaltui/tidal_client.py`) — thin wrapper over `tidalapi` session (search, favorites, playlists, artist/album lookups, lyrics, track URLs with `TooManyRequests` passthrough).

## Testing

No pytest suite. Verification options:
1. `python -m compileall tidaltui/` and import the app module.
2. Headless Textual pilot tests (see `/tmp/opencode/test_cycle_*.py` from the hjkl fix) — mount `ContentArea` in a bare `App`, `await area.replace(screen)`, `pilot.press(...)`, assert on `tabs.active`, `app.screen.focused`, `ListView.index`. Requires a terminal-capable environment only for the terminal size; Textual's `run_test` is otherwise headless.
3. Running the real app needs a TIDAL session; don't do it in CI.

## Gotchas

- `query_one(TabbedContent)` on `ContentArea` would match a *hidden* screen's tabs — always go through `stack[-1]` (this was an actual bug in the hjkl fix).
- Textual `ListView.index` is `None` until the first cursor move on an empty list; don't assert on it before populating.
- `DataTable` does not bind `l`/`h` (only `left`/`right` arrows), which is why lowercase h/l are free for tab cycling.
- Focus is lost (set to None) when the focused widget is hidden by a pane switch; auto-focus only kicks in when nothing was focused — that's why `cycle_tabs` focuses explicitly.
