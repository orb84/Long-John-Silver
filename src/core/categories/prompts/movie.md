# Movie category guidance

## Metadata and local-state workflow

- For factual questions about a movie (cast, lead actors, directors, writers, release dates, ratings, IDs, artwork), prefer the generic `metadata_lookup` tool with `media_type="movie"` before general web search.
- Use movie-owned workflows for local library state, upgrades, metadata refreshes, and download actions.
- Resolve metadata through movie-owned providers before torrent selection when title/year identity is ambiguous.
- Prefer exact title/year matches and reject releases that look like unrelated TV, software, books, games, music, or archive-only payloads.
- Use destructive movie actions only after explicit confirmation.

## Movie release-name skill

- Movie torrent titles usually encode title, release year, edition/cut, resolution, source, HDR/DV tags, audio codec/channels, video codec, language/subtitle tags, and release group.
- Identity is normally title plus release year. Use metadata context to disambiguate remakes, same-title shorts, foreign/original titles, alternate local titles, and two-part sequel names.
- Edition/cut tags are identity-relevant when the user asks for them: `Director's Cut`, `Final Cut`, `Theatrical`, `Extended`, `Unrated`, `Criterion`, `IMAX`, `Remastered`, `Restored`, `Open Matte`, and similar.
- Source/quality tags are preference evidence, not identity: `REMUX`, `BluRay`, `BDRip`, `WEB-DL`, `WEBRip`, `HDTV`, `DVDRip`, `CAM`, `TS`, `TC`, `SCR`, `R5`. Reject CAM/TS/telesync/screener unless the user explicitly accepts low-quality/pre-release sources.
- HDR/DV tags such as `HDR10`, `HDR10+`, `Dolby Vision`, `DV`, and `HLG` matter for compatibility but do not replace title/year correctness.
- Audio/video tags such as `x264`, `x265`, `HEVC`, `AV1`, `AAC`, `AC3`, `EAC3`, `DTS`, `TrueHD`, `Atmos`, and channel counts are useful quality facets after identity/language constraints.

## Movie language and collection skill

- Language tags are compressed in release titles: `ITA`/`Italian`/`Italiano` means Italian; `ENG`/`English` means English; `MULTI`, `dual-audio`, `DLMux`, or `MUX` can indicate multiple audio tracks but should not be treated as stronger than explicit requested-language evidence.
- A movie request for Italian should prefer explicit Italian or inspected multi-audio evidence. Unknown language is risky when the user explicitly asked for a language.
- For movie collection, box-set, saga, trilogy, anthology, franchise, director set, actor set, or other multi-film torrent results, preserve the release/collection identity and each source movie filename. Do not rename every child movie to a broad user query or tag.
- Collection handling must be based on payload structure and payload/file evidence with distinct parsed movie identities, not on marketing words in the release title alone.
- Treat covers, screenshots, samples, NFOs, subtitles, and artwork as auxiliary payloads, not primary movies to import as standalone library entries.
- If a torrent contains multiple distinct films, inspect or preserve the file list; do not flatten them into one movie unless the category has clear metadata evidence that it is one film split across parts/discs.
