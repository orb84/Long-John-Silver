General Files is a narrow catch-all category, not a bypass.

Use it only when the user gives a concrete miscellaneous target that does not belong to a richer installed category. Good targets include exact filenames, documents, PDFs, archives, datasets, manuals, lectures, audio releases, and other one-off payloads.

Behavior rules:
- Richer installed categories win. Do not use General for obvious TV seasons/episodes, movies, or any future category that clearly owns the request.
- Do not silently reinterpret a failed TV/movie search as General merely because the richer search failed. Ask before changing category.
- Preserve literal names, extensions, quoted terms, version numbers, edition tags, and format words in the query.
- When calling `search_media_torrents`, pass `category_id: "general"` for General requests.
- Do not append the global media language automatically. Include language only when the user explicitly made it part of the target.
- Prefer candidates with exact title/format match and healthy seeders.
- Reject installers, executable packages, scripts, cracks, keygens, activators, credential dumps, and suspicious software payloads.
- For archives or bundles, inspect the file list when the useful payload is unclear.
- If more than one plausible candidate remains, summarize candidate IDs with title, size, and seeders and ask the user to choose.
