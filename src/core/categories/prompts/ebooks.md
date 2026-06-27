# Ebooks category guidance

## Ebook release-name skill

- Ebook torrent/search results are edition-sensitive. Identity may require author, title, series name/number, translator, publisher, edition, publication year, language, ISBN, and file format.
- Common result shapes include `Author - Title.epub`, `Title - Author [EPUB]`, `Author - Series 01 - Title`, `Title (Year) [Retail EPUB]`, `Title [EPUB MOBI AZW3 PDF]`, `Title scan OCR PDF`, and comic-style `CBZ`/`CBR` archives.
- EPUB/AZW3/MOBI/PDF/DJVU/CBZ/CBR are not interchangeable. EPUB/AZW3 are usually reflowable reading formats; PDF/DJVU often mean fixed-layout scans; CBZ/CBR usually mean comics, manga, art books, or image-based payloads.
- `Retail`, `clean epub`, `converted`, `scan`, `OCR`, `illustrated`, `annotated`, `revised`, `expanded`, `collector`, `omnibus`, and `box set` are edition clues. Preserve them when the user asked for that edition or reader compatibility depends on them.
- Multi-format ebook torrents are acceptable when they contain the requested format. Do not reject a candidate just because it also includes AZW3/MOBI/PDF if EPUB is present and requested.
- Language matters for books. Use requested/configured language when present; do not silently accept a visibly different translation/language.
- For series/omnibus requests, verify whether the user wants one volume, a specific book number, an omnibus, or the whole series before queueing a large bundle.

## Ebook safety and import skill

- Reject audiobooks, music albums, movies, TV episodes, software, executable installers, cracks, and suspicious scripts when the user asked for ebooks.
- Covers, OPF metadata, and safe companion files can be kept, but executable payloads should never be imported or run.
- Preserve author/title/series/format metadata. Do not flatten a multi-book bundle into one broad query folder if the file list contains distinct books.
