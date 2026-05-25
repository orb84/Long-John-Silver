# Seed-in-place library sharing

Long John Silver now treats library sharing as an explicit, opt-in path rather than a side effect of downloading. The first supported mode is **seed in place**.

Seed in place means that new torrent-backed files are written directly into the relevant category library folder and left byte-for-byte compatible with the original torrent payload. LJS does not rename or rewrite those files after completion. Plex, Jellyfin, Infuse, and similar media servers can still present the library with clean metadata even when release folders or filenames are imperfect.

## Product goals

The feature exists for users who want to keep participating in the torrent ecosystem after files enter their library.

The user should be able to choose one of two paths during first-run setup and later in Compass:

1. **Private Library Mode**: LJS behaves like a normal media manager. Downloads use the staging directory, are organized into the library, seed according to the normal short policy, and staging copies can be cleaned up.
2. **Fair Share Mode / Seed in place**: new torrent downloads are stored under category library roots and remain connected to their original torrents. LJS keeps reporting ratios, upload rates, peers, paths, and seed status in a dedicated Sharing view.

This first implementation intentionally does **not** implement hardlink, symlink, or reflink overlay modes. Those can be added later behind the same policy model after filesystem capability detection exists.

## Storage behavior

When seed-in-place sharing is enabled for a category, LJS resolves the libtorrent save path as:

```text
<category library root>/<item display name>/Season NN/
```

For non-episodic items, the `Season NN` segment is omitted.

The torrent’s own internal folder and file names remain untouched, except for LJS’s temporary `.downloading` suffix while the torrent is incomplete. When the torrent reaches 100%, the suffix is restored and the files are considered library files. Pausing a torrent or shutting down LJS must **not** remove `.downloading`; partial files should remain visibly partial until libtorrent reports the torrent is a seed. Round 26 also adds a startup repair that reapplies `.downloading` to old paused/queued/stalled rows that lost the suffix in earlier builds.

The existing download staging directory remains in use when sharing is disabled, when a category does not support a usable library root, or when the item has no category.

## Bandwidth behavior

Library sharing has a separate upload quota from active downloads.

- `default_quality.max_upload_speed_kbps` limits upload capacity for active downloading torrents.
- `sharing.library_upload_speed_kbps` limits upload capacity for completed library seeds.
- The torrent engine splits each quota across active handles in that transfer class.
- When both active-download uploads and library-seed uploads have finite caps, the libtorrent session cap is set to their sum. If either active class is uncapped, LJS leaves the session cap uncapped and enforces the capped class with per-handle fallback limits so the uncapped class is not throttled accidentally.
- `pause_when_downloading` now actively suppresses library seed upload while active downloads are running; it is not just a UI preference.

This prevents completed library seeds from consuming the same upload budget that active downloads need for peer reciprocity.

## Settings model

The global settings contain a `sharing` object:

```yaml
sharing:
  enabled: true
  mode: seed_in_place
  library_upload_speed_kbps: 500
  active_seed_slots: 4
  seed_ratio_target: 2.0
  seed_duration_hours: 168
  pause_when_downloading: false
  category_overrides:
    tv: true
    movie: true
```

`category_overrides` is optional. Missing category keys inherit the global `enabled` value.


## Domain boundary

The long-term domain object for this feature is a `TorrentLibraryBinding`: a relationship between an infohash, the untouched torrent payload path, the category library item, and the sharing policy applied to that item. The Round 24 implementation stores the first binding fields directly on `DownloadItem` (`save_path`, `sharing_enabled`, `uploaded_bytes`, and `seed_ratio`) so the feature is usable immediately without a large migration. Future work should promote those fields into a dedicated binding repository once per-file repair, existing-library import, and overlay modes exist.

## Download row additions

Download records now persist sharing telemetry:

- `save_path`: the libtorrent save root used for the torrent.
- `sharing_enabled`: whether this download is a seed-in-place library share.
- `uploaded_bytes`: cumulative uploaded bytes reported by libtorrent.
- `seed_ratio`: cumulative upload/download ratio.

These fields make the Sharing UI resilient to page reloads and allow future LLM tools to report seeding state without reaching into libtorrent directly.

## UI surfaces

### First-run setup

The Automation step includes a Sharing & Seeding choice:

- **Private Library Mode** keeps default behavior.
- **Fair Share Mode** enables seed-in-place sharing.

The same step asks for the dedicated library upload cap, active shared-library slots, and ratio goal using plain language. It also sits beside the new launch-at-login checkbox so always-on media boxes can opt into both sharing and boot startup without hunting through Compass first.

### Compass

Compass includes a dedicated **Sharing & Seeding** panel. Users can enable/disable the mode, adjust the library upload cap, active shared-library item count, ratio target, seed duration, and pause-while-downloading behavior. Copy should stay user-facing: explain that seed-in-place means "share the library copy" rather than referring to internal overlay modes.

### Sharing view

The main navigation includes a **Sharing** view. It shows:

- sharing enabled/disabled state,
- library upload quota,
- active seed slots,
- total uploaded bytes,
- per-item ratio,
- current upload rate,
- seeds/peers,
- library save path,
- torrent title and media identity.

## Safety rules

The library organizer must not mutate seed-in-place payloads. Once a torrent is using seed-in-place mode, completion handling marks the files as organized at their actual payload path and skips staging cleanup.

Future delete flows should offer separate choices:

1. remove from library only,
2. stop sharing and delete payload,
3. keep sharing but hide from the visible library.

## Future work

Good next extensions are:

- LLM tools: `manage_library_sharing` and `verify_library_share`. `list_library_shares` is implemented as the read-only reporting surface.
- Per-category sharing toggles in the Compass panel.
- Filesystem checks for hardlink/symlink/reflink overlay modes.
- Broken-binding detection when a user manually deletes or moves seed-in-place files.
- NAT/port status and share-health diagnostics in the Sharing view.
