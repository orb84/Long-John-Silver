# Soulseek / slskd Integration Design

Round 134 turns the earlier slskd boundary into the intended invisible user flow: when a user enables Soulseek, LJS installs a native slskd binary, writes a safe `slskd.yml`, starts slskd with the app, and stops the owned process during app shutdown.

## Runtime model

Soulseek support is backed by **managed slskd**, not Docker and not a fake torrent adapter.

- LJS downloads a platform-specific slskd zip from the official GitHub releases API.
- LJS extracts it under `data/slskd/bin`.
- LJS writes `data/slskd/slskd.yml` from `SoulseekSettings` and the share planner.
- Managed slskd writes completed transfers to the user-selected LJS download folder (`settings.download_dir`) and partial transfers to `<download_dir>/.slskd-incomplete`; LJS imports completed files into category libraries. The app directory is only for the slskd binary/config/logs, not download payloads.
- LJS generates internal web/API/JWT secrets automatically.
- Users only need to provide Soulseek username/password and sharing preference.
- The managed web/API endpoint is local-only: `http://127.0.0.1:5030`.
- LJS starts slskd when Soulseek is enabled and closes the owned process on shutdown.

Manual/remote slskd remains possible by setting `soulseek.managed=false` in advanced config, but the normal UI path is managed.

## Architecture boundary

Soulseek remains a source-provider family, not a Jackett indexer and not a torrent fallback bolted into libtorrent.

Implemented boundary:

- `SoulseekSettings` in global settings.
- `src/integrations/slskd_manager.py` for install/config/start/stop/health.
- `src/integrations/slskd_client.py` for slskd API calls and tolerant result normalization.
- `src/integrations/slskd_config.py` for share/download config planning and YAML rendering.
- Agent tools:
  - `search_soulseek`
  - `enqueue_soulseek_download`
  - `get_soulseek_share_plan`
- Settings/setup UI fields for Soulseek credentials and share mode.
- Background download-health integration for stalled torrent companion search.

The existing `queue_download` tool remains torrent-only. Soulseek candidates must use `enqueue_soulseek_download` so libtorrent is never asked to process a non-magnet transfer.

## Setup UX

Initial setup and Compass/Settings now follow the same behavior:

1. User enables Soulseek.
2. User enters Soulseek username/password.
3. User chooses share mode.
4. LJS auto-generates slskd API/JWT/web secrets.
5. LJS installs/starts slskd automatically.
6. LJS validates that slskd actually authenticated to the Soulseek network.
7. Disabling Soulseek stops the managed process and prevents startup auto-launch.

Soulseek account creation is not treated as a separate OAuth/registration API. The UI explains that users can provide an existing account, or try a new unique username/password and let the Soulseek network accept/reject it. LJS does not mark Soulseek as ready until slskd confirms a network login. Rejected credentials become `auth_failed`, with a clear recoverable error in setup/settings.

## Sharing policy

Default: share the LJS library root under a neutral alias. This makes Soulseek reciprocity useful without asking users to understand `slskd.yml` immediately.

Safety rules:

- Filesystem roots such as `/` are rejected.
- Downloads and incomplete directories are excluded automatically from shares.
- User-selected exclusions are preserved.
- Paths are aliased before slskd exposes them to other Soulseek users.
- Secret values are redacted in previews.
- slskd HTTP is bound to `127.0.0.1`; the API key is limited to loopback CIDRs.

## Search/result policy

LJS filters out inaccessible Soulseek files before they are surfaced as agent candidates.

Filtered out by default:

- locked files;
- payloads exposed only through `lockedFiles`-style response buckets;
- private/restricted files requiring extra access conditions;
- duplicate rows for the same user + file path.

Still allowed:

- queueable results where the uploader currently has no free upload slot;
- queueable results with a queue length, because they can still be downloaded later through Soulseek semantics.

## Stalled-torrent behavior

When a torrent stalls for too long, LJS:

1. parks the torrent instead of deleting it;
2. periodically re-tests it in a bounded health window;
3. searches for better torrent alternatives;
4. for Soulseek-enabled categories, runs a companion Soulseek search when torrent retries came up empty;
5. notifies the user about viable Soulseek options without pretending they are torrent queue rows.



## Round 137 login-detection hardening

The managed runtime now validates slskd login state from the explicit server-state fields exposed by slskd (`server.isConnected` / `server.isLoggedIn`) and no longer blocks searches merely because the cached status is `checking`. A successful Soulseek search is treated as live proof that the account works. Definitive failures still block safely: missing credentials and credential rejection.

Managed startup also passes Soulseek credentials through both generated YAML and `SLSKD_SLSK_USERNAME` / `SLSKD_SLSK_PASSWORD` environment variables, and restarts slskd when those credentials change while the process is already running.

## Current limitation

LJS still does not present Soulseek transfers as first-class download rows in the same way as libtorrent downloads. That remains the next major step:

- add a `SourceTransferMonitor` abstraction;
- add a `SlskdTransferMonitor` implementation;
- import completed Soulseek files through category post-import hooks;
- report Soulseek transfer progress in the unified downloads UI without lying about the backend.

## Round 142 album-search notes

Soulseek search is literal and folder-oriented. LJS should not search Soulseek
with explanatory prompt words such as `album`, `track`, `song`, `download`, or
format filler unless those words are part of the title. Music searches now build
small query variants from concise artist/title text and may try both
`artist title` and `title artist` orders.

slskd search results can represent an album as many files in the same remote
folder. LJS now preserves the remote folder path, groups multiple audio files
from the same user/folder into a folder candidate, and lets
`enqueue_soulseek_download` receive a `filenames` array so the whole album can be
queued rather than only a single track.

Because Soulseek peer availability changes over time, no-match searches can now
create a deduplicated recurring assistant condition check. This reuses the
normal scheduled-task system and samples different times of day/week without
adding a separate hidden scheduler.

## Round 144 response-shape hardening

Real slskd search responses may be keyed by username or by folder rather than returned as one flat `SearchResponseItem` list. LJS now preserves inherited username and remote-folder context while normalizing results, so a folder such as `Persiana Jones - 1999 - Puerto Hurraco/...` becomes a folder/album candidate with all track filenames plus safe sidecars.

For Music, direct Soulseek searches also clean the first query and retry concise variants. Request-shape words such as `album`, `track`, `download`, and quality filler should not be sent to Soulseek unless they are genuinely part of the title.

## Round 145 response-materialization fix

A real slskd deployment can return search state with non-zero `responseCount` and `fileCount` while the state payload still contains `responses: []`. LJS now treats that as “raw results exist but response details are not materialized yet,” not as “nothing found.” It polls the dedicated response endpoint, uses the protocol token only as a fallback, and writes a local sanitized diagnostic dump if slskd reports files that LJS still cannot parse.
