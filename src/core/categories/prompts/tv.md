# TV category guidance

## Metadata and local-state workflow

- For stable factual questions about a TV show (cast, lead actors, creators, known seasons, known episodes, ratings, IDs, artwork), prefer the generic `metadata_lookup` tool with `media_type="tv"` before general web search.
- For current public questions — rumours, renewal/cancellation, production status, shooting/filming, creator/showrunner interviews, delays, next-season information, next-episode schedules, or public chatter — metadata alone is not enough. Use `category_web_research` and preserve the user's exact focus in its `query` argument.
- For next-episode questions, use TV-owned providers/episode lists first, then corroborate with official/platform/reference pages when needed. Never derive a schedule by adding seven days unless a fetched source explicitly states the cadence and dates.
- For “next/upcoming/future season” questions, never treat an already-released season page as the upcoming answer. Compare season/episode air dates and article/source dates against the runtime current date.
- Use TV-owned workflows/context for local library state, missing episodes, aired/unaired checks, and download actions.
- Resolve episodic metadata through TV-owned providers before searching for downloads.
- Do not download unaired episodes.
- Use destructive TV actions only after explicit confirmation.

## TV release-name skill

- TV torrent titles usually encode the series title first, then a season/episode marker, then language, quality/source, codec, audio, and release-group tags.
- Validate the series-title portion before `SxxEyy`, `Sxx`, `1x02`, `Season N`, or similar markers. If the requested show name appears only after the episode marker, it may be an episode title from a different series.
- Exact single-episode formats include `S01E02`, `s01e02`, `1x02`, `1.02`, `Season 1 Episode 2`, `Show.Name.S01E02`, and `Show - 1x02 - Episode Title`.
- Multi-episode files may look like `S01E01E02`, `S01E01-E02`, `S01E01-02`, `S01E01.E02`, or `Episodes 1-2`.
- Season packs and ranges may look like `S01`, `Season 1`, `Season 01`, `S01 Complete`, `Season 1 Complete`, `Complete Season 1`, `S01E01-E06`, `S01E01-06`, `S01E01 E06`, or `S01-S03`.
- Complete-series containers may look like `S01-S05`, `Complete Series`, `Complete Collection`, or `All Seasons`. Use them only when the user asked for a whole series or when the category explicitly exposes file-level selection/priority for the requested unit.
- For a full-season request, prefer one verified season pack/range over scattered single episodes when it covers the requested season and has acceptable language, quality, size, and seeders.
- For a single explicit episode request, prefer an exact `SxxEyy` release. A season pack/range is acceptable only when it clearly contains that episode and selective file priority/import can keep the requested unit.
- Do not treat a later random episode as a substitute for early missing episodes. If the user asks for a season from the start, S01E01/S01E02 coverage matters more than isolated later episodes.

## TV language skill

- Tracker titles often reduce languages to short tags. `ITA`, `iTALiAN`, `Italian`, and `Italiano` mean Italian. `ENG`, `English`, and `Inglese` mean English. Other common compressed tags include `FRE`/`FRA` for French, `GER`/`DEU` for German, `SPA`/`ESP` for Spanish, `JPN` for Japanese, and `KOR` for Korean.
- `SUB`, `SUBBED`, `VOST`, `VOSTFR`, `FORCED`, and subtitle-language tags are subtitle evidence, not proof of matching audio.
- `MULTI`, `dual-audio`, `DL`, `DLMux`, `MUX`, or similar terms imply possible multiple audio tracks; they are useful but weaker than an explicit requested/preferred language tag unless the title or inspected file evidence also names that language.
- If the configured language is English, ITA+ENG or MULTI is only a fallback unless inspected file/audio evidence proves the requested English track is present and preferred-only English options are weak or unavailable.
- If the user does not explicitly override language, use the show's configured language and the existing audio languages in the library context.
- Do not silently queue a visibly different single-language release. Unknown language should be presented/asked about for automatic or uncertain cases.
- Seeders matter after hard constraints: among equivalent coverage/language/quality candidates, recommend the healthier seeded release.

## Automation safety

- TV background release watching is per-show opt-in. Do not infer that library shows, missing-episode suggestions, metadata updates, global automation permission, or previous manual downloads allow searching or queueing.
- Only explicit user approval or the show's own enabled auto-download setting can trigger unattended episode searches/downloads.
- If the user asks manually for a TV download, that one request is allowed; it does not enable future tracking or auto-download for the show.
