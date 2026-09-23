from flask import Flask, render_template, url_for

from . import files, notes_api, player, prefs
from .config import BASE_DIR, ROOT
from .store import migrate_legacy_state


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    migrate_legacy_state()

    for module in (files, prefs, player, notes_api):
        app.register_blueprint(module.bp)

    @app.context_processor
    def asset_helpers():
        def asset(filename: str) -> str:
            """Static URL with an mtime query so edits bypass the browser cache."""
            try:
                version = int((BASE_DIR / "static" / filename).stat().st_mtime)
            except OSError:
                version = 0
            return url_for("static", filename=filename, v=version)
        return {"asset": asset}

    @app.route("/")
    def index():
        return render_template("index.html", code_dir=str(ROOT), root_name=ROOT.name or str(ROOT))

    return app
