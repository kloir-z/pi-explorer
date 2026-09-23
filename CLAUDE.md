# CLAUDE.md

> **CLAUDE.md編集ポリシー**: このファイルは最小限に保つ。内容を追加・修正する前に、参照先ドキュメント（docs/ 等）に書くべきかユーザーに確認すること。

## 行動指針

- **サービス再起動**: `explorer/` やフロントエンドを変更した場合、動作確認のためサービスを再起動する。Windowsでは `powershell -Command "Start-Process powershell -ArgumentList '-Command','Restart-Service pi-explorer' -Verb RunAs"`、Raspberry Piでは `sudo systemctl restart pi-explorer.service`。

## Project overview

Pi Explorer is a web file explorer + media player for one local folder tree (ROOT). Flask backend + vanilla JS frontend, port 5125. Layout and setup: see README.md.

## Key decisions

- Every API takes `path` relative to ROOT. Resolve it only through `explorer/paths.py:resolve_rel` (segment-level `..` rejection + separator-aware `contained`).
- Writes under any `.git` segment are refused (`has_git_component`) so hooks/config can't be planted in repos under ROOT.
- `static/js/*.js` are classic scripts sharing one global scope (inline `onclick` handlers call them). Load order in `templates/index.html` matters; `app.js` is last and calls `init()`.
- ROOT resolution: `PI_EXPLORER_ROOT` → `config.local.json` `root_dir` → default `/home/user/code/` (legacy `GIT_VIEWER_CODE_DIR` / `code_dir` still accepted).
