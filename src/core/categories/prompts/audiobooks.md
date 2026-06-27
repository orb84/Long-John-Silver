# Audiobooks category guidance

## Audiobook release-name skill

- Audiobook identity requires more than book title. Track author, title, narrator/reader, abridged vs unabridged status, language, series/order, duration, publisher, and chapter support when available.
- Common result shapes include `Author - Title - Narrator [M4B]`, `Title by Author read by Narrator`, `Author - Series 01 - Title`, `Title Unabridged M4B`, `Title MP3 64kbps`, `Title Chaptered M4B`, and folders of numbered MP3 parts/chapters.
- M4B/M4A are common Apple-friendly audiobook containers; M4B often carries chapter markers. MP3 folder releases can be valid but may need track/chapter order. FLAC audiobooks can be high quality but are often inconvenient and large.
- `Unabridged` means the full text; `abridged` means shortened. Treat abridgement as an identity constraint, not a minor quality flag.
- Narrator/reader is identity-relevant. Do not satisfy a request for a specific narrator with a different reader unless the user accepts alternatives.
- Chaptering matters. Prefer chaptered M4B/M4A or well-ordered MP3 folders when the user cares about audiobook playback.
- Language matters for audiobooks. Use requested/configured spoken language; subtitle or ebook-language evidence does not prove audiobook language.

## Audiobook safety and import skill

- Reject ebooks/PDFs/EPUBs, music albums, movies, TV episodes, software, installers, and unrelated archives when the user asked for an audiobook.
- Preserve folder order, part numbers, chapters, cue files, cover art, and metadata sidecars when safe.
- Do not lossy-transcode MP3 to another lossy format automatically. Only convert lossless or source-preserving sidecars according to category profile and user request.
