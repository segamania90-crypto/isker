**[English]** | [Русский](README.ru.md) | [Español](README.es.md)

# ISKER — AI Agent for Routine Development Tasks

A language-agnostic AI agent that takes over routine coding work: writing
functions from a description, project-wide search and rename, refactoring,
fixing bugs from a description or log, generating tests and documentation.

Works **the same way** with any text file — GDScript (Godot), Python,
HTML/CSS/JS, etc. Language specifics are defined at the prompt level, not
in the tool code, so moving to a new stack doesn't require reworking the
agent itself.

Runs on **free tiers** through a chain of three providers
(Groq → OpenRouter → Gemini) with automatic fallback and retries.

> This is a pet project for a portfolio, but also a real tool the author
> uses in their own projects. It does **not replace** the developer's
> understanding of the code — anything requiring creative decisions
> (architecture, visual work, asset choices) is still done by hand.

---

## Screenshot

![ISKER screenshot](docs/screenshot.png)

---

## Features

- **Desktop interface** built with PySide6 — chat window, conversation
  history, step progress indicator (`[3/15]`), cancel button.
- **Ready-made Windows `.exe`** — download and run without installing
  Python (see "Quick Start" below).
- **Three interface languages**: Russian, English, Spanish — switch on
  the fly, no restart needed. The agent replies in the language the
  question was asked in, regardless of the UI language.
- **Task summary** after each run — what was done and why, shown as a
  separate block in the chat.
- **Provider fallback chain with retries**: if one provider is
  unavailable, the agent automatically switches to the next one; if all
  three are down, it waits and retries (5, then 15 seconds) before
  reporting an error.
- **Caching** of repeated requests within a session.
- **Cancel button**: a long-running task can be interrupted between
  steps — without rolling back changes already applied, with an honest
  report of what was completed.
- **CLI mode** (`main.py`) — for testing the core from a terminal,
  without a graphical interface.

---

## Quick Start (no Python installation needed)

1. Go to the [Releases](../../releases) section of this repository.
2. Download the `ISKER-windows.zip` archive from the latest release.
3. Extract the **whole archive** to any folder.
4. Get at least one free API key and fill in the `.env` file inside the
   extracted folder — see "Getting API keys" below, step by step.
5. Launch `ISKER.exe`.

> **Important:** the `.exe` does not work on its own — the `_internal`
> folder (all required libraries) must sit right next to it. If you
> want to move the app elsewhere (e.g. to your Desktop): either copy
> the **whole folder** (`ISKER.exe` + `_internal`), or leave the folder
> where it is and create a **shortcut** to `ISKER.exe` instead (right
> click → "Send to" → "Desktop, create shortcut") — this is easier for
> future updates.
>
> On first launch, Windows SmartScreen may show a warning ("Windows
> protected your PC") — this is the standard reaction to an `.exe` from
> an independent developer without a paid code-signing certificate, not
> a sign of a virus. Click "More info" → "Run anyway".

---

## Getting API keys and setting up `.env`

You need at least **one** key from one of the three providers below —
all three are completely free, sign-up is done through a normal browser,
no credit card required. Having all three at once simply lets the agent
automatically switch between them if one is overloaded — it's not
required to get started.

- **Groq**: https://console.groq.com/keys
- **OpenRouter**: https://openrouter.ai/keys
- **Gemini**: https://aistudio.google.com/apikey

Go to the site of the provider you picked, sign up, find a button like
"Create API key" / "Get API key" — copy the resulting string (it usually
starts with something like `gsk_...` or `AIza...`).

### How to put it into `.env` (no coding experience needed)

1. In the extracted folder, find the file `.env.example`.
2. Copy it and rename the copy to `.env` (important: without `.example`
   at the end — the leading dot before "env" must stay).
3. Open `.env` with any text editor — plain old Notepad works fine
   (right-click the file → "Open with" → "Notepad").
4. Find the line for your provider and type the key right after the `=`
   sign, for example:
   ```
   GROQ_API_KEY=gsk_your_real_key_here
   ```
5. Save the file (`Ctrl+S`) and close Notepad.

Lines for providers whose keys you didn't get can be left empty or
commented out (`#` at the start of the line) — the agent simply won't
try that provider.

### ⚠️ Model names in `.env.example` may become outdated

Providers periodically retire older model versions. If you see
`LLM FAIL ... reason=HTTP 404` in the logs for some provider, it means
the model name set in `.env` no longer exists. The agent will
automatically fall back to the next provider in the chain, but for full
functionality it's worth updating `.env` with a current model name.
Up-to-date model lists:

- Groq: https://console.groq.com/docs/models
- OpenRouter (filter by `:free`): https://openrouter.ai/models
- Gemini: https://ai.google.dev/gemini-api/docs/models

---

## Installing from source (for developers)

```bash
git clone <this-repo-url>
cd isker
python -m venv venv
source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env` with your key — see "Getting API keys and setting up
`.env`" above.

### Running the GUI version

```bash
python -m ui.app
```

### Running the CLI version (for terminal testing)

```bash
python main.py
```

### Building your own `.exe`

```bash
pyinstaller --name ISKER --windowed --add-data "assets;assets" --hidden-import ui.app --icon assets/icon.ico run_gui.py
```

The finished `.exe` will appear in `dist/ISKER/` together with the
`_internal` folder — both parts must be moved together (see the warning
above).

---

## Before working with the agent

**Always make a `git commit`** (or save a manual project copy) before
allowing the agent to write to files. The app will remind you of this
when starting a session with write access enabled, but responsibility
for saving your work rests with the human — the agent does not run git
commands itself.

When starting a session:
1. Point it to the project's root folder.
2. Allow write access to files (or leave it read-only — the default).
3. Give tasks in free form, e.g.: "find every place that uses health and
   show them to me" or "fix the bug: NullReferenceException in
   PlayerController.gd on line 42".

Atomicity: if the agent is interrupted mid-way through a multi-step edit
(error, manual cancel) — changes already applied are **not rolled back
automatically**; you only get an honest list of what was completed.
Rollback happens through your saved `git commit` checkpoint.

---

## Tests

```bash
pytest
```

---

## Project structure

```
agent/       # core: LLM wrapper with fallback/retries, agent loop, memory
tools/       # universal, language-agnostic file/text tools
ui/          # desktop interface built with PySide6
assets/      # font, icon
tests/       # pytest
main.py      # CLI entry point
run_gui.py   # entry point used to build the GUI into an .exe
```

---

## Known limitations

This is a pet project running on free tiers, not a commercial product —
the limitations below are conscious design choices, not bugs:

- **Free provider limits** are finite. If all three providers are
  overloaded at once, the agent will wait and retry twice (up to ~20
  seconds), but if that doesn't help, it will report an error and
  suggest trying again later.
- **No per-file rollback** mid multi-step edit — only a full-session
  rollback through `git`, done by the user.
- **Doesn't read screenshots/images** — the free-tier models in the
  current provider chain don't support this well enough.
- **Doesn't bulk-scrape websites** — deliberately, due to anti-bot
  protection and instability when page markup changes. It can read the
  content of a single page given an explicit link.
- Visual and creative work (scene layout, asset selection, architectural
  decisions) is not something the agent does — that stays with the
  developer.

---

## License

MIT
