# Persona Packages

LJS personas are package folders. A package owns the assistant prompt, display name, avatar, and a bounded set of UI theme hints. This keeps the user-facing assistant layer coherent while preserving the app's stable layout and accessibility rules.

## Folder layout

Canonical layout:

```text
config/personas/<persona_id>/
  persona.md
  persona.json
  avatar.png
  theme.json
```

Loose `config/personas/<persona_id>.txt` prompt files are no longer part of the open-source baseline. A persona must be a folder package so prompt text, metadata, avatar, and theme hints travel together.

Persona ids may contain only letters, numbers, `_`, and `-`. This keeps filesystem lookup and avatar serving safe.

## `persona.md`

`persona.md` is the prompt/personality text placed at the top of assistant system prompts. It should define the assistant's voice, how it addresses the user, and any stable communication rules.

Example:

```md
You are Long John Silver, a sharp-witted quartermaster.
Address the user as "Captain".
Be warm, direct, and competent.
```

`persona.md` is the fixed canonical filename. Other prompt filenames are intentionally ignored to keep packages predictable.

## `persona.json`

`persona.json` describes the package without exposing raw prompt text to the UI.

```json
{
  "id": "long-john-silver",
  "display_name": "Long John Silver",
  "description": "A warm pirate quartermaster who helps manage the library.",
  "version": 1,
  "avatar": "avatar.png"
}
```

Fields:

- `display_name`: shown in the app header and settings selector.
- `description`: shown in the settings preview.
- `version`: package version for maintainers.
- `avatar`: optional local avatar filename. If omitted, LJS looks for `avatar.png`, `avatar.jpg`, `avatar.jpeg`, or `avatar.webp`.

## Avatar files

Avatars must be local files inside the persona folder. Allowed extensions are:

- `.png`
- `.jpg`
- `.jpeg`
- `.webp`

If the avatar is missing or invalid, the UI falls back to the bundled static LJS icon. The app never trusts absolute paths or parent-directory escapes from `persona.json`.

## `theme.json`

`theme.json` holds the persona's bounded presentation theme. The bundled default package mirrors the current website colors here, so the visible app mood can follow the active persona instead of being a hardcoded one-off in the UI.

Theme files may use nested `colors` and `styles` objects:

```json
{
  "colors": {
    "bg_deep": "#050a15",
    "accent_gold": "#f4a261",
    "accent_gold_glow": "rgba(244, 162, 97, 0.15)",
    "accent_teal": "#2a9d8f",
    "accent_teal_glow": "rgba(42, 157, 143, 0.2)",
    "accent_red": "#e76f51",
    "accent_red_glow": "rgba(231, 111, 81, 0.15)",
    "ocean_center": "#0c1a38",
    "ocean_mid": "#050a15",
    "ocean_edge": "#03060d",
    "text_main": "#f8f9fa",
    "text_dim": "#adb5bd",
    "glass_bg": "rgba(14, 23, 44, 0.6)",
    "glass_border": "rgba(255, 255, 255, 0.08)",
    "nav_bg": "rgba(0, 0, 0, 0.25)",
    "bubble_bg": "rgba(255, 255, 255, 0.06)",
    "compass_bg": "rgba(5, 10, 21, 0.7)"
  },
  "styles": {
    "avatar_shape": "freeform",
    "background_style": "ocean",
    "panel_style": "glass",
    "chat_bubble_style": "quartermaster"
  }
}
```

Top-level color/style keys are also accepted for convenience, but new packages should prefer the nested form above.

Allowed color keys:

- `accent`, `accent_gold`, `accent_gold_glow`
- `accent_teal`, `accent_teal_glow`
- `accent_red`, `accent_red_glow`
- `background_deep`, `bg_deep`
- `ocean_center`, `ocean_mid`, `ocean_edge`
- `glass_bg`, `glass_border`
- `text_main`, `text_dim`, `text`, `text_muted`
- `gold`, `teal`, `border`
- `nav_bg`, `bubble_bg`, `compass_bg`

Allowed style keys:

- `avatar_shape`: `freeform`, `rounded`, `circle`, or `square`.
- `background_style`
- `panel_style`
- `chat_bubble_style`

The frontend maps those keys onto known CSS variables only. Persona packages cannot inject arbitrary CSS or JavaScript.

## Runtime behavior

The active persona is stored in `Settings.active_persona`. The backend uses `PersonaRegistry` as the single source of truth for prompt text, metadata, avatar paths, and theme hints.

Frontend flow:

1. The app calls `GET /api/personas/active` during boot.
2. The header display name and avatar are updated from the active package.
3. Sanitized theme hints are mapped onto known CSS variables.
4. The Compass settings panel lists packages from `GET /api/personas`.
5. Changing persona calls `POST /api/personas/active`.

Backend prompt flow:

1. `AIAssistant` reads `settings.active_persona`.
2. `PromptBuilder` creates a `PersonaContext`.
3. `PersonaContext` resolves the package through `PersonaRegistry`.
4. The resolved `persona.md` text is placed at the top of user-facing prompts.

## Design rule

Persona packages own presentation flavor, not app structure.

They may control:

- assistant prompt/personality;
- assistant display name;
- avatar;
- bounded app theme colors;
- future greeting/error/chat-bubble hints.

They must not control:

- navigation layout;
- accessibility constraints;
- arbitrary CSS or JavaScript;
- category behavior;
- tool permissions;
- filesystem paths outside their package folder.
