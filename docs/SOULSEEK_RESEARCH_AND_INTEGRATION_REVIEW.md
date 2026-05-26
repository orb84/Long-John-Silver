# Soulseek Research and Integration Review

Status: **Not implemented in Round 118**.

Soulseek could become a useful Music/Audiobook companion source, but it is not a drop-in torrent fallback. This pass deliberately stops at research and architecture design because a safe integration requires user decisions and a separate source-adapter model.

## What Soulseek is operationally

Soulseek is a peer-to-peer file-sharing network whose catalog is whatever users choose to share. It has a long history around music communities, especially independent, underground, rare, live, bootleg, mixtape, and DJ-set material, but the protocol and social expectations differ from torrents.

Important differences from torrents:

- Downloads are generally **single-source**, not swarmed from many peers.
- Search results point to files/folders shared by a specific user.
- Queue position, user upload slots, bans, privacy, and share reciprocity matter.
- A folder download can expand into many individual file transfers.
- Completed path structures vary by client and folder-download mode.
- Sharing is a first-class concern, not a hidden implementation detail.

Soulseek itself states that users should only share and download files they are legally allowed to share/download. Any integration must make that explicit in setup and never present Soulseek as a way around copyright rules.

## Client options reviewed

### slskd

slskd is the most promising integration target because it is a daemon/client-server application for Soulseek, can run in Docker, exposes a web UI, supports remote operation, and is designed for token-secured access. It also already models searches, downloads, queues, and shared folders as a service rather than requiring LJS to become a Soulseek client.

Integration implications:

- LJS should talk to an existing slskd instance/API, not implement the Soulseek protocol.
- Setup must collect endpoint, token/API key, Soulseek username/password or confirm those are already configured in slskd.
- Path mapping is required: slskd download folders and LJS staging/library folders may be inside different containers/hosts.
- Sharing folders must be explicitly configured and sandboxed.

### Nicotine+

Nicotine+ is a mature graphical Soulseek client with strong protocol knowledge and headless options, but it is less natural as an LJS backend because it is primarily a desktop client. It remains valuable as protocol/reference behavior and as a manual companion app.

### Direct protocol implementation

Do **not** implement the reverse-engineered protocol in LJS. In plain terms: do not implement the reverse-engineered protocol directly inside LJS. The protocol is proprietary, old/rigid, and coordination-sensitive. A direct implementation would increase maintenance and interoperability risk without helping the category architecture test.

## Required design before implementation

Soulseek needs a separate source adapter, not a torrent provider bolted onto Jackett.

Proposed future interface:

```text
SourceProvider
├── TorrentProvider
│   └── Jackett / direct scraper candidates
└── SoulseekProvider
    └── slskd adapter candidates
```

Soulseek candidates should expose:

- source type: `soulseek`
- remote user
- remote folder path
- file list / folder list
- file sizes
- queue/free-slot status when available
- bitrate/format hints parsed from filenames and folder names
- client-side download ID(s)
- staging path mapping

They should not pretend to be torrent magnets.

## Required user decisions

The following choices are significant enough that this pass should not silently decide them:

1. **Client backend:** slskd is the likely backend, but the user must choose Docker/native/existing instance.
2. **Soulseek credentials:** whether LJS stores credentials or only talks to an already-configured client.
3. **sharing policy:** which folder is shared, whether downloads are shared back, and what bandwidth/slot limits apply.
4. **Path model:** dedicated Soulseek staging/download directory vs shared download folder; host/container path mappings.
5. **Privacy/legal UI:** explicit notices, logs, and clear “legal content only” expectations.
6. **Queue behavior:** when to retry, abandon, or prefer torrents if a Soulseek peer queues indefinitely.
7. **Library import:** whether a folder download keeps user/folder prefixes or is normalized immediately into the category library.

## Recommended future integration shape

1. Add a `soulseek` source-provider service declaration, probably starting in Music only.
2. Add slskd endpoint/token settings under the category or a shared `sources.soulseek` config.
3. Add a dedicated Soulseek staging root under `<download_dir>/soulseek` or a user-selected path.
4. Add queue-aware candidate ranking that understands single-source risk and waits.
5. Add a share sandbox with explicit allowed roots.
6. Add reconciliation from slskd completion events/polling into the same category `download_target_for_item` and post-import hooks used by torrents.
7. Keep the legal/terms notice visible in setup and provider help.

## Round 118 decision

Not implemented in Round 118. The safe work completed here is documentation and architecture guidance only. Soulseek looks feasible via slskd, but not decision-free, and it should not be hacked into the torrent pipeline.
