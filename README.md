<div align="center">
  <img src="docs/assets/ljs-avatar.png" alt="Long John Silver" width="220" />

# Long John Silver

**A self-hosted AI crewmate for your media library. You talk; he finds it, fetches it, sorts it onto the shelf, and remembers it later.**

*Agentic pirating — done with manners.*

[Repository](https://github.com/orb84/Long-John-Silver) · [Maintainer](https://github.com/orb84) · [Support](SUPPORT.md) · [License](LICENSE)
</div>

---

## Why this exists

I built Long John Silver — **LJS** — for a very specific kind of person: my family.

They live in countries where streaming barely covers anything. The catalogues are thin, the show they want isn't there, and so every few weeks someone would message me: *"can you get me this?"* And I'd go off to a torrent tracker — past the fake download buttons, the scam ads, the "your computer has a virus" popups — find a sane release, download it, and ship it over. Again and again.

LJS is me trying to put that whole job into a box you can talk to. You set it up once on a computer at home. You connect it to WhatsApp or Discord. Then your non-technical aunt in another country just messages it — *"is there a new episode of For All Mankind? in Italian if you can"* — and it handles the rest. No trackers, no ads, no scams, no me.

It's also, frankly, a media butler for people like us who already self-host. It auto-grabs new episodes of shows you follow, suggests things, answers questions about a series, and keeps your library tidy — all from plain conversation instead of a wall of rules and config screens.

The reason it works by *conversation* and not by config is partly an accident of where I was. I was deep in a completely different project — one built around a custom LLM harness and agents doing real work — and living in that tech every day. So when the "can you get me this?" problem landed for the hundredth time, reaching for an agent to solve it felt like the obvious move, not a clever one. LJS is what happened when those two things collided: the tools I already had in my hands, pointed at a chore I was tired of doing by myself.

> A small honest note: people kept telling me this was "an AI Sonarr/Radarr." I had to go look those up — I didn't know the *arr stack existed until I already had LJS working. So if you know that world, yes: think of LJS as the **conversational, AI-native** cousin. You don't write rules and quality profiles and regex; you just *ask*, and a language model does the reasoning while the app does the careful, boring, safe parts.

### Seeding back, on purpose

Torrenting and Soulseek only work because people share back. LJS takes that seriously: it's built to **re-share what you download** automatically and safely — seeding in place from your own library, sharing back over Soulseek — so you stay a good citizen of the communities you're leaning on, without having to think about it. Take, but also give.

> ⚓️ **Legal bit, plainly:** LJS ships no media, no tracker accounts, no API keys, and no right to access anything. It's an empty boat. What you do with it is on you — only use sources and downloads you're actually allowed to access where you live.

---

## What it feels like to use

You talk to it. That's the whole interface. A few real examples:

- *"What am I missing from the latest season of Severance?"*
- *"Grab it in Italian, 1080p is fine."*
- *"Why did you suggest this one?"*
- *"Remind me in a week if the finale's out yet."*
- *"Check again in three weeks whether there's a better release, and tell me."*
- *"Download the new Radiohead album, lossless if you can find it."*

It searches your configured sources, ranks what it finds, picks a sensible release, downloads it, drops it into the right folder with a sane name, and tells you when it's done. Follow-ups stick to the conversation — *"no, the first one"* or *"actually, the released version"* attach to what you were just doing instead of starting over.

<p align="center">
  <img src="docs/assets/screenshot-helm-chat.png" alt="The Helm — chat" width="32%" />
  <img src="docs/assets/screenshot-suggestions.png" alt="Suggestions" width="32%" />
  <img src="docs/assets/screenshot-booty-library.png" alt="The Booty — library" width="32%" />
</p>

The web dashboard carries the theme: the **Helm** is where you chat, the **Booty** is your library, the **Compass** is settings, and the **Voyage Logs** are where you see what actually happened. The pirate is light seasoning, not a costume — he calls you Captain, drops the occasional "arr," and otherwise gets out of the way.

---

## How it works, in one breath

There's exactly one idea holding the whole thing together:

> **The language model reasons and chooses. The app validates, stores, executes, and protects. Categories define what each kind of media means. Chat bridges just carry messages back and forth.**

That separation is the point. The LLM is good at understanding *"the missing episodes from the latest season, in Italian, but a better release than last time."* It is **not** trusted to touch your filesystem, queue a download, or invent facts. It proposes; the app checks everything against real rules and only then acts. Raw, messy torrent results never get dumped into the model — it sees compact, validated candidates and a short list of safe next moves.

You bring the brain (an LLM) and the sources (your indexers). LJS is the careful crew in between.

---

## The honest state of things

This is a real project I use, but it's still early and I'd rather tell you the truth than oversell it. Here's where things actually stand:

| Works well today | Working, but improving | Still rough |
|---|---|---|
| Finding and downloading things | Suggestions & taste learning (it's getting smarter about what you like, but don't expect magic yet) | **Subtitles** — searchable, but handling isn't good yet |
| Getting the **right audio language** (this was the original problem; it's solid) | First-run setup is functional but not yet friendly | |
| Auto-following shows for new episodes | | |
| TV & Movies (the most battle-tested categories) | Music, Audiobooks, Ebooks (newer, real, less mileage) | |
| Discord chat (well tested) | Telegram & WhatsApp bridges (real, less tested) | |

**Translation:** the core loop — *ask → find → download → file it away → follow it* — is the strong part. The polish around the edges is in progress. Bug reports and feedback genuinely help and are very welcome.

---

## Getting it running

You need a computer that stays on — a home server, a mini-PC, an old laptop in a drawer, a NAS. Then:

```bash
git clone https://github.com/orb84/Long-John-Silver.git
cd Long-John-Silver
./run.sh                 # macOS/Linux: sets up everything, starts on http://localhost:8088
```

On **Windows** (Command Prompt or PowerShell):

```bat
run.bat                  REM Sets up everything, starts on http://localhost:8088
```

That's it for the first launch. The launcher finds (or installs) a compatible Python 3.10+, builds an isolated environment, installs dependencies, and starts the server. Open `http://localhost:8088` in a browser and the first-run wizard takes it from there.

A few extra launcher tricks if you need them:

```bash
./run.sh 9000            # use a different port
LJS_PORT=3000 ./run.sh   # port via environment variable
./run.sh install         # (re)install dependencies only
./run.sh update          # update dependencies
./run.sh doctor          # print diagnostics if something's off
./run.sh reset-venv      # nuke the environment and rebuild it next start
```

```bat
run.bat 9000             REM custom port
run.bat install          REM (re)install dependencies
run.bat update           REM update dependencies
run.bat doctor           REM diagnostics
run.bat install-python   REM install Python 3.11 via winget
run.bat install-ffmpeg   REM install FFmpeg
run.bat reset-venv       REM rebuild the environment next start
```

**FFmpeg** is only needed if you want Music/Audiobook format conversion. The launcher tries to install it for you; if that fails, LJS still runs and just marks conversion as unavailable. To skip the attempt: `LJS_AUTO_INSTALL_FFMPEG=0 ./run.sh` (or `set LJS_AUTO_INSTALL_FFMPEG=0` before `run.bat`).

---

## First-run setup

The wizard walks you through the essentials. There are really only two things LJS *needs*, plus a pile of optional niceties.

### 1. A language model (required)

LJS needs an OpenAI-compatible chat endpoint to do its thinking. You have three honest paths:

- **Free, to start with → NVIDIA NIM.** NVIDIA hosts capable models for free through their developer program and API catalogue. It's the easiest way to try LJS without spending anything. Grab a key from [build.nvidia.com](https://build.nvidia.com/) ([NIM docs](https://docs.nvidia.com/nim/index.html)), point the wizard at the NIM base URL, and go.
- **Fully local → LM Studio, Ollama, vLLM, llama.cpp.** Run a model on your own machine and nothing leaves the house. LJS is a *tool-using* assistant, not just a chatbot, so give it a reasonably capable model and a decent context window.
- **Hosted → OpenRouter and other OpenAI-compatible providers.** LJS can route different jobs to different models — a fast cheap one for understanding what you said, a stronger one for the tricky download decisions.

> LJS reads the real context window from your provider when it can, and budgets prompts to fit it. When a conversation gets long it **compresses** old history rather than silently forgetting it.

### 2. Where your library lives (required)

You set one **library root**, and each category (TV, Movies, etc.) defaults to a folder under it — `library/TV Shows`, `library/Movies`, and so on — unless you override it. LJS creates the folders for you.

### 3. The optional good stuff

None of these are mandatory, but they make LJS noticeably smarter:

| Service | What it buys you |
|---|---|
| **TMDB** | Strongly recommended. Real titles, posters, years, cast, seasons, ratings. Without it, recognition and artwork are much weaker. |
| **Jackett / Torznab** | Aggregates your torrent indexers into one search. This is your main download source. |
| **Soulseek / slskd** | Optional companion source (see below) — great for music, audiobooks, ebooks, and rare releases. |
| **TVMaze** | TV episode schedules and aired/upcoming checks. |
| **Trakt** | Watch-state and taste signals. LJS ships a public app ID, so you just link your account with a PIN. |
| **Plex** | Optional library refresh and watch-state sync. |
| **OpenSubtitles** | Subtitle search (handling is still rough — see honesty section). |
| **SearXNG** | Optional, self-managed web research — lets the assistant look things up online (titles, schedules, ambiguous releases) without sending you to a search engine. LJS can install and run it for you; auto-install is tested on Linux so far. |
| **MusicBrainz · Cover Art Archive · Discogs · AcoustID** | Music metadata and cover art. |
| **Open Library · Google Books · Gutendex · Internet Archive · LibriVox · Apple Books · Comic Vine** | Book, audiobook, and comic metadata. Most are keyless. |

Real keys and private paths go into **local, git-ignored** config that the UI writes for you. The repo only ever contains blank templates — your secrets never get committed.

---

## What LJS knows about: categories

LJS is **category-first**. A "category" isn't just a filter — it's a self-contained expert on one kind of media. It owns its own metadata sources, folder naming, search vocabulary, quality rules, and the way it talks to the LLM. The core app deliberately knows *nothing* about what a "season" or an "album" is; the category does. (More on why that matters in the builders' section below.)

Out of the box:

- **TV Shows** — seasons, episodes, aired-vs-missing logic, season packs, full-series containers. Tracked shows **auto-download new episodes** by default (you can switch any show to notify-only). Names land like `TV Shows / Show Name / Season 02 / Show Name - S02E04.mkv`. TMDB strongly recommended.
- **Movies** — title/year matching, posters, quality upgrades, language and subtitle preferences. TMDB strongly recommended.
- **Music** — artists, albums, singles, EPs, discographies. Understands FLAC/ALAC/AAC/MP3, bitrate, sample rate. Optional Apple-friendly conversion that keeps the original and writes a sidecar (e.g. ALAC `.m4a`). Keyless metadata via MusicBrainz.
- **Audiobooks** — narrator, abridged/unabridged, chapters, M4B/M4A/MP3. Knows it is *not* the same as an ebook.
- **Ebooks** — EPUB, PDF, AZW3, MOBI, DJVU, plus comics (CBZ/CBR). Author/ISBN/edition/translator aware.
- **General Files** — the careful catch-all for things that don't fit a richer category: manuals, datasets, public-domain archives, lectures. Deliberately conservative; preserves original filenames; inspects anything ambiguous before queueing.

---

## The sharing ethic, and Soulseek

LJS leans on community sources, so it's built to give back.

**Seeding** is handled automatically: downloaded files are seeded in place from your own library according to your settings, so you keep ratio and keep releases alive instead of leeching and vanishing.

**Soulseek** is an optional companion source, managed for you through **slskd** (a headless Soulseek client). When you enable it, LJS quietly:

- downloads and installs the right slskd binary for your platform;
- generates its secrets and writes a safe config;
- starts it with LJS and stops it when LJS exits;
- shares back what you choose (defaulting to your library root);
- keeps Soulseek transfers cleanly separate from torrents.

All you provide is a Soulseek username and password (existing or new). For music, Soulseek is often the better first stop for single tracks and normal albums; torrents stay better for big discographies and bundles. Audiobooks, ebooks, and exact searches can use it too.

---

## Talking to it from anywhere

The web Helm is the home base, but the whole point is reaching it from your phone. LJS speaks over:

- **Web** (dashboard + chat — and the UI works on a phone browser, not just desktop)
- **Discord** (well tested)
- **Telegram** and **WhatsApp** (real, less tested)
- REST / WebSocket for the adventurous

Every one of these is just a *messenger*. They all talk to the exact same assistant brain underneath — same reasoning, same memory, same tools, same safety. A bridge only knows how to receive a message, identify who sent it, format the reply for its platform, and send progress updates. It never gets its own private logic. So your aunt on WhatsApp gets the same competent crewmate you get in the browser.

---

## Keeping it on

For testing, run it by hand. For real use, you want it up after a reboot. The Compass has a **start-at-login** checkbox that writes a per-user entry — a LaunchAgent on macOS, a `.desktop` autostart file on Linux desktops, or a current-user `Run` key on Windows. It's not a privileged system service, and it tells you exactly what it wrote.

For headless servers, NAS boxes, or Docker, use a proper supervisor (systemd unit, container restart policy) instead — that's documented in [`docs/AUTOSTART_BOOT_INTEGRATION.md`](docs/AUTOSTART_BOOT_INTEGRATION.md).

The goal: once it's set up, the assistant, the scheduler, your reminders, your auto-following shows, and your imports all just keep working without you babysitting a terminal.

---
---

# For builders

The rest of this is for people who want to extend LJS or understand how it's put together. If you just want to use it, you're already done — happy hoarding.

## The one rule: the core never knows your domain

Everything good about LJS's design comes from one stubborn rule:

> **The core application must never contain category-specific meaning.** It stores generic envelopes. It does not know what a TV episode, a movie edition, an album track, a book chapter, a game's DLC, or a "missing season" is.

When a generic piece of code is tempted to write `if category == "tv": ...`, that's a bug in the making. The knowledge lives in the **category**, behind hooks the core calls without understanding. The core asks *"what's in the library for this item?"* and the category hands back a canonical object it built. The core asks *"where does this file go?"* and the category answers.

**Why bother with this discipline?** Because the original version *was* media-first, and it hurt. Every new idea meant touching scanning, search, the scheduler, the UI, and the agent all at once, and every fix in one place quietly broke another. Pulling all domain meaning into self-contained categories is what makes LJS extensible instead of a pile of special cases. A new kind of media should be a new category — not a hundred new `if` statements scattered across the codebase.

This is enforced, not just hoped for: there are guard scripts (`scripts/check_category_architecture.py` and friends) that fail the build if category-specific logic leaks into generic layers.

## Adding a category (the fun part)

This is the headline extension point, and it's designed to be approachable. A category owns:

- its identity (id, display name, keywords, item types);
- its setup requirements and settings;
- how it reads files off disk and builds a canonical library object;
- how it interprets provider metadata;
- its search vocabulary and how it judges candidates;
- its folder naming and import paths;
- its lifecycle and suggestion rules;
- the guidance it gives the LLM;
- optional workflows that become UI buttons or suggested actions.

The preferred path is **definition-backed**: you write a declarative YAML contract, not a pile of Python.

- `config/category-definitions/<id>.yaml` — the shareable contract (what it is, its providers, its formats, its LLM guidance).
- `config/category-config-templates/<id>.yaml` — blank safe defaults for the private, git-ignored per-user config.

Use `extends:` for is-a relationships (Audiobooks and Ebooks both extend a shared `book` definition) and `mixins:` for additive capabilities (Audiobooks mix in `audio` to get FFmpeg conversion; Ebooks don't). Real API clients and rich object builders still live in category-owned Python, but a surprising amount is just the contract.

The assistant can even help you build one. The flow (see [`skills/category_creation_guide.md`](skills/category_creation_guide.md)) is: research the domain's metadata and download conventions, draft a `CategorySpec`, **preview** the scaffold, get your approval, then **apply** it — nothing is written to disk until you say so. Want podcasts, video games, or photo archives? That's a category, not a fork.

Before you start, read these in order:

1. [`architecture.md`](architecture.md) — the living architecture contract (long, opinionated, worth it)
2. [`AGENTS.md`](AGENTS.md) — the engineering standards
3. [`skills/category_creation_guide.md`](skills/category_creation_guide.md) — the category workflow
4. [`docs/RELEASE_MAINTENANCE_REVIEW.md`](docs/RELEASE_MAINTENANCE_REVIEW.md)

## Architecture at a glance

```text
Web / Discord / Telegram / WhatsApp / REST
        │
        ▼
Shared ChatSessionRunner      ← one chat brain for every surface
        │
        ▼
AIAssistant
        ├── LLM intent routing & language handling
        ├── compact conversation / active-goal memory
        ├── category context & prompt guidance
        └── a small, generic tool catalogue
                │
                ▼
       Contract-bound tool execution   ← validates everything the LLM asks for
                │
      ┌─────────┼──────────┐
      ▼         ▼          ▼
 Categories  Search /    Downloads
 (the brains  indexers    queue + import
  of a domain)   │          │
      ▼         ▼          ▼
 Canonical   Candidate   Category-owned
 library     workspace   safe paths
 objects   (raw payloads stay here, out of the LLM)
```

The boundaries that matter:

- Generic code must not branch on TV/movie/album/book semantics.
- The LLM must call registered tools with schema-valid arguments — no invented paths, no raw tracker dumps.
- Every chat surface goes through one shared runner; bridges don't own planning, timeouts, or memory.
- Downloaded files can never escape their configured category root.

The agent's download surface is deliberately tiny — `category context → search_media_torrents → queue_download` — instead of dozens of micro-tools. The category supplies the meaning; the LLM supplies the judgement; three generic tools do the work.

## Repository layout

```text
src/
  ai/               assistant runtime, prompt/context builders, tool contracts, tools
  core/             config, database, downloader, scheduler, library logic
    categories/     the category system — bases, concrete categories, hooks, workflows
  integrations/     TMDB, TVMaze, Trakt, Plex and other metadata adapters
  llm_providers/    OpenAI-compatible provider abstraction, key store, task routing
  search/           Jackett/Torznab, browser strategies, web-search helpers
  utils/            auth, browser runtime, bencode, quality, parsing, safety helpers
  web/              FastAPI app, routers, chat bridges, templates, frontend
config/
  settings.template.yaml          public fresh-install template, no secrets
  settings.local.yaml             your live settings (git-ignored)
  category-definitions/           shareable category contracts
  category-config-templates/      blank safe defaults for private category config
  categories/                     your live per-category settings (git-ignored)
  personas/                       assistant persona packages
migrations/         SQLite migrations applied on startup
scripts/            architecture, regression, and release-readiness checks
skills/             category-creation guidance used by the assistant
```

## Config & secrets

Public templates and private runtime config are kept strictly apart. The repo tracks templates only; the UI writes your real values into git-ignored local files (`config/settings.local.yaml`, `config/categories/*.yaml`, `data/`). The repo must never contain real keys, tokens, library entries, or personal paths — there's a `scripts/check_public_docs.py` guard for exactly that.

Most configuration belongs in the UI or YAML. The supported environment variables are intentionally few:

```bash
LJS_PORT=8088
LJS_HOST=0.0.0.0
LJS_ACCESS_LOGS=quiet            # quiet | verbose
LJS_WEB_SECRET=<random secret for signed auth tokens>
LJS_AUTO_INSTALL_FFMPEG=0        # skip launcher FFmpeg auto-install
LJS_AUTO_INSTALL_PYTHON=0        # skip launcher Python auto-install
LJS_PYTHON=/path/to/python3.11   # pin a specific interpreter
LJS_VENV_DIR=.venv               # override the virtualenv directory
```

(There's also a `.env.example` if you prefer wiring things that way.)

## Dev checks

```bash
python -m compileall src scripts main.py
python scripts/check_public_docs.py
python scripts/check_category_architecture.py
python scripts/check_ai_intent_architecture.py
python scripts/check_security_architecture.py
python scripts/check_architecture.py
```

Full `pytest` needs the complete dependency set from `requirements.txt` and may not run in restricted sandboxes without optional runtime bits like `aiosqlite` or `libtorrent`. The `tests/` tree mirrors `src/`, and there's a long trail of scenario-trace scripts (`scripts/round*_*.py`) that replay real bugs to keep them dead.

## Security & privacy

- Designed for local / self-hosted use. Your traffic and library stay yours.
- Destructive actions require confirmation and return receipts.
- Safe-path enforcement stops any download from escaping its configured roots.
- API keys and OAuth tokens are never committed.
- Browser/web-search helpers fail soft with typed errors instead of crashing the chat loop.
- See [`SECURITY.md`](SECURITY.md) for reporting and hardening notes.

---

## License

Long John Silver is free and open-source software under the **GNU Affero General Public License v3.0 or later** (`AGPL-3.0-or-later`).

```text
Copyright © 2026 orb84 and contributors
```

See [LICENSE](LICENSE), [NOTICE](NOTICE), and [AUTHORS.md](AUTHORS.md).

## Support & contact

- Repository: <https://github.com/orb84/Long-John-Silver>
- Maintainer: <https://github.com/orb84>
- Contact: <orblaboratories@gmail.com>
- If LJS saves you time — or makes your media hoard a little less haunted — there are ways to send a coffee my way in [SUPPORT.md](SUPPORT.md). It genuinely helps cover the bills (and my Claude habit). Any PayPal to orblaboratories@gmail.com is hugely appreciated.

---

<div align="center">

*Still early, still being hammered on. Bug reports and feedback are worth their weight in doubloons — don't expect a perfect maiden voyage, but do tell me where she leaks.*

</div>
