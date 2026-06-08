# TV category guidance

- For stable factual questions about a TV show (cast, lead actors, creators, known seasons, known episodes, ratings, IDs, artwork), prefer the generic `metadata_lookup` tool with `media_type="tv"` before general web search.
- For current public questions — rumours, renewal/cancellation, production status, shooting/filming, creator/showrunner interviews, delays, next-season information, next-episode schedules, or public chatter — metadata alone is not enough. Use `category_web_research` and preserve the user's exact focus in its `query` argument.
- The `intent` passed to `category_web_research` is a semantic hint, not an enum. Use natural labels when helpful; the category/LLM planner maps the actual wording to a search plan.
- Search TV current-public questions like a researcher: include the exact title, season/episode number when known, current year, streamer/network, and focus terms such as renewed, confirmed, production, filming, shooting, showrunner, creator, interview, casting, premiere, air date, official, press, Deadline, Variety, or Hollywood Reporter.
- For Apple TV+ shows, include Apple TV / TV+ / Apple press or `site:tv.apple.com` where useful, but also check reputable trades/news for production or interview information.
- For next-episode questions, use TV-owned providers/episode lists first, then corroborate with official/platform/reference pages when needed. Never derive a schedule by adding seven days unless a fetched source explicitly states the cadence and dates.

- For “next/upcoming/future season” questions, never treat an already-released season page as the upcoming answer. Compare season/episode air dates and article/source dates against the runtime current date. If the latest known season is already in the past, the user is asking about the following season unless they explicitly named the past season.
- Separate source types clearly: official/trade/reference evidence can support confirmation; Reddit/X/forum/fan calendars only support unconfirmed chatter unless corroborated.
- Use TV-owned workflows for local library state, missing episodes, aired/unaired checks, and download actions.
- Resolve episodic metadata through TV-owned providers before searching for downloads.
- Do not download unaired episodes.
- Prefer exact SxxEyy matches for episode requests; use season packs only when explicitly requested or when many missing episodes make a pack appropriate.
- Use destructive TV actions only after explicit confirmation.
