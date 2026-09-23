# Pi Explorer

Web file explorer and media player for a local folder tree. Built for Raspberry Pi, also runs as a Windows service.

## Features

- **Files** -- browse any folder under the root with per-folder sort, favorites, and preview of code (highlight.js), Markdown (marked.js, Mermaid), HTML (rendered in an iframe), images (incl. HEIC), PDF, audio and video. Text files can be edited in place.
- **Player** -- audio/video with synced SRT subtitles (speaker-colored cue cards), a chapter index from a sibling `chapters.json`, autoplay / continuous play, and a per-file playback log.
- **Reading aids** -- bookmarks on Markdown sentences, SRT cues and code lines; per-file notes stored as a sidecar `<file>.notes.md`.
- **Listen** -- dashboard of every `output.mp3` under the folder open in Files, aggregating playback logs into listened coverage % (union of played spans) and furthest-reach %, grouped by parent folder, with unlistened/in-progress/done filters.

## Setup

```bash
pip install -r requirements.txt
python app.py                    # http://localhost:5125
```

The served root defaults to `/home/user/code/`. To change it, either:

- set the `PI_EXPLORER_ROOT` environment variable, or
- copy `config.local.json.example` to `config.local.json` and edit `root_dir` (gitignored, per-machine).

The environment variable wins. The older names `GIT_VIEWER_CODE_DIR` / `code_dir` are still honored.

Favorites, bookmarks and navigation settings are stored in `data/` (gitignored).

### systemd (Raspberry Pi)

```bash
sudo cp pi-explorer.service /etc/systemd/system/
sudo systemctl enable --now pi-explorer.service
```

### Windows service

From an elevated PowerShell: `scripts/install-deps.ps1`, then `scripts/install-service.ps1` (NSSM). Re-run `install-service.ps1` after moving the checkout.

## Layout

- `app.py` -- entry point
- `explorer/` -- Flask app: `files`, `prefs`, `player`, `notes_api` blueprints; `paths` (containment checks), `store` (JSON state), `notes` (notes file format)
- `templates/index.html` -- page shell
- `static/js/` -- vanilla JS, one file per area, loaded in order
- `static/style.css` -- GitHub Dark theme
