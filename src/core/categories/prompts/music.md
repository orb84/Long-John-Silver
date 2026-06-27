# Music category guidance

## Music release-name skill

- Music torrent/Soulseek results may represent an artist, album/release group, exact release/edition, single, EP, soundtrack, live recording, bootleg, or complete discography. Do not flatten all of these into a single title string.
- Identity often needs artist plus album/release title. Exact release identity may also need year, label, catalog number, country, medium, remaster/edition, and tracklist.
- Common music result shapes include `Artist - Album (Year) [FLAC]`, `Artist - Album [24bit-96kHz]`, `Artist - Album (Deluxe Edition)`, `Artist - Album - WEB FLAC`, `Artist - Discography`, `Artist - Album CD1/CD2`, and folders containing numbered tracks.
- A `release group` is the overall album concept; a `release` is a specific issued version. Remasters, deluxe editions, vinyl/CD/WEB editions, regional releases, and expanded editions can be different releases of the same album.
- Preserve edition terms such as `Deluxe`, `Expanded`, `Remaster`, `Anniversary`, `Explicit`, `Clean`, `Vinyl`, `CD`, `WEB`, `Live`, `Bootleg`, `OST`, `Original Soundtrack`, `Score`, and catalog/label identifiers when the user asks for them.
- Quality/format tags include `FLAC`, `ALAC`, `WAV`, `AIFF`, `APE`, `AAC`, `MP3`, `Opus`, `OGG`, `320kbps`, `V0`, `VBR`, `CBR`, `16bit`, `24bit`, `44.1kHz`, `48kHz`, `96kHz`, and `192kHz`.
- Prefer lossless FLAC/ALAC/WAV/AIFF when the user asks for best quality or lossless. Prefer MP3/AAC/Opus only when the user asks for portable/lossy compatibility or the category profile says so.
- For album requests, prefer complete album folders with sensible track numbering and safe companion files (`cue`, `log`, artwork, playlists) over scattered single files.
- For a single-track request, do not download a whole album/discography unless the user asked for the album, wants context/sidecars, or the file list proves selective queueing is safe.
- Global movie/TV spoken-language defaults are normally irrelevant to music search. Only use language for music when the user explicitly asks for a language-specific release, lyrics, vocal language, subtitles, or a category profile says language is relevant.

## Music safety and import skill

- Reject movies, TV episodes, music videos, ebooks, audiobooks, software, installers, cracks, scripts, and unrelated archives when the user asked for music.
- Preserve multi-disc structure and track order. Do not rename all tracks to the album title; keep track titles/numbers and safe sidecars.
- For discography/complete-catalog requests, warn that many albums may be involved and prefer presenting candidates or requiring explicit confirmation before queueing a very large bundle.
