from __future__ import annotations

import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash


F = TypeVar("F", bound=Callable[..., Any])
TEHRAN_TIMEZONE = timezone(timedelta(hours=3, minutes=30))
MAX_UPLOAD_FILES = 5
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    session_token TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    visibility TEXT NOT NULL DEFAULT 'private'
        CHECK (visibility IN ('private', 'public')),
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS shares (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (file_id, user_id)
);
CREATE INDEX IF NOT EXISTS files_owner_id_idx ON files(owner_id);
CREATE INDEX IF NOT EXISTS shares_user_id_idx ON shares(user_id);
"""


def format_local_datetime(value: str) -> str:
    utc_datetime = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    local_datetime = utc_datetime.astimezone(TEHRAN_TIMEZONE)
    return local_datetime.strftime("%Y/%m/%d %H:%M")


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("K1BOX_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=os.path.join(app.instance_path, "k1box.sqlite3"),
        UPLOAD_FOLDER=os.path.join(app.instance_path, "uploads"),
        MAX_UPLOAD_FILES=MAX_UPLOAD_FILES,
        MAX_CONTENT_LENGTH=None,
    )
    if config:
        app.config.update(config)

    app.add_template_filter(format_local_datetime, "local_datetime")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"], timeout=10)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return cast(sqlite3.Connection, g.db)

    app.get_db = get_db  # type: ignore[attr-defined]

    @app.teardown_appcontext
    def close_db(_exception: BaseException | None = None) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        user_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()
        }
        added_admin_column = "is_admin" not in user_columns
        if added_admin_column:
            db.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )
        if "must_change_password" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN must_change_password "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "session_token" not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN session_token TEXT NOT NULL DEFAULT ''"
            )
        for row in db.execute(
            "SELECT id FROM users WHERE session_token = ''"
        ).fetchall():
            db.execute(
                "UPDATE users SET session_token = ? WHERE id = ?",
                (secrets.token_urlsafe(32), row["id"]),
            )

        admin = db.execute(
            "SELECT id FROM users WHERE username = ?", ("admin",)
        ).fetchone()
        if admin is None:
            db.execute(
                """
                INSERT INTO users
                    (username, password_hash, is_admin, must_change_password, session_token)
                VALUES (?, ?, 1, 1, ?)
                """,
                ("admin", generate_password_hash("admin"), secrets.token_urlsafe(32)),
            )
        elif added_admin_column:
            db.execute(
                """
                UPDATE users
                SET password_hash = ?, is_admin = 1, must_change_password = 1
                WHERE id = ?
                """,
                (generate_password_hash("admin"), admin["id"]),
            )
        db.commit()

    @app.before_request
    def load_user_and_check_csrf() -> Any:
        user_id = session.get("user_id")
        user = (
            get_db().execute(
                "SELECT id, username, is_admin, must_change_password, session_token "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if user_id is not None
            else None
        )
        if user is not None and session.get("session_token") == user["session_token"]:
            g.user = user
        else:
            if user_id is not None:
                session.clear()
            g.user = None
        if request.method == "POST":
            submitted = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not expected or not secrets.compare_digest(submitted, expected):
                abort(400, description="Invalid request. Refresh the page and try again.")
        if (
            g.user is not None
            and g.user["must_change_password"]
            and request.endpoint not in {"change_password", "logout", "static"}
        ):
            return redirect(url_for("change_password"))

    @app.context_processor
    def add_template_helpers() -> dict[str, Callable[[], str]]:
        def csrf_token() -> str:
            if "_csrf_token" not in session:
                session["_csrf_token"] = secrets.token_urlsafe(32)
            return cast(str, session["_csrf_token"])

        return {"csrf_token": csrf_token}

    def login_required(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return cast(F, wrapped)

    def admin_required(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if g.user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login"))
            if not g.user["is_admin"]:
                abort(403)
            return view(*args, **kwargs)

        return cast(F, wrapped)

    @app.get("/")
    def index() -> Any:
        return redirect(url_for("dashboard") if g.user else url_for("public_files"))

    @app.route("/register", methods=["GET", "POST"])
    def register() -> Any:
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not re.fullmatch(r"[A-Za-z0-9_]{3,30}", username):
                flash("Username must be 3–30 characters and contain only letters, numbers, or underscores.", "error")
            elif len(password) < 8:
                flash("Password must be at least 8 characters long.", "error")
            else:
                try:
                    cursor = get_db().execute(
                        """
                        INSERT INTO users (username, password_hash, session_token)
                        VALUES (?, ?, ?)
                        """,
                        (
                            username,
                            generate_password_hash(password),
                            secrets.token_urlsafe(32),
                        ),
                    )
                    get_db().commit()
                except sqlite3.IntegrityError:
                    flash("That username is already taken.", "error")
                else:
                    session.clear()
                    session["user_id"] = cursor.lastrowid
                    session["session_token"] = get_db().execute(
                        "SELECT session_token FROM users WHERE id = ?",
                        (cursor.lastrowid,),
                    ).fetchone()["session_token"]
                    flash("Your account has been created. Welcome!", "success")
                    return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="register")

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if g.user:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = get_db().execute(
                "SELECT id, username, password_hash, session_token "
                "FROM users WHERE username = ?",
                (username,),
            ).fetchone()
            if user is None or not check_password_hash(user["password_hash"], password):
                flash("Incorrect username or password.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session["session_token"] = user["session_token"]
                flash("You are now logged in.", "success")
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="login")

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("public_files"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password() -> Any:
        if request.method == "POST":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirmation = request.form.get("confirm_password", "")
            user = get_db().execute(
                "SELECT password_hash FROM users WHERE id = ?", (g.user["id"],)
            ).fetchone()
            if not check_password_hash(user["password_hash"], current_password):
                flash("Current password is incorrect.", "error")
            elif len(new_password) < 8:
                flash("New password must be at least 8 characters long.", "error")
            elif new_password != confirmation:
                flash("New passwords do not match.", "error")
            else:
                get_db().execute(
                    """
                    UPDATE users
                    SET password_hash = ?, must_change_password = 0,
                        session_token = ?
                    WHERE id = ?
                    """,
                    (
                        generate_password_hash(new_password),
                        secrets.token_urlsafe(32),
                        g.user["id"],
                    ),
                )
                get_db().commit()
                updated_session_token = get_db().execute(
                    "SELECT session_token FROM users WHERE id = ?", (g.user["id"],)
                ).fetchone()["session_token"]
                session.clear()
                session["user_id"] = g.user["id"]
                session["session_token"] = updated_session_token
                flash("Your password has been changed.", "success")
                return redirect(url_for("dashboard"))
        return render_template(
            "change_password.html",
            required=bool(g.user["must_change_password"]),
        )

    @app.get("/dashboard")
    @login_required
    def dashboard() -> Any:
        owned_files = get_db().execute(
            """
            SELECT files.*, COUNT(shares.user_id) AS share_count
            FROM files
            LEFT JOIN shares ON shares.file_id = files.id
            WHERE files.owner_id = ?
            GROUP BY files.id
            ORDER BY files.uploaded_at DESC
            """,
            (g.user["id"],),
        ).fetchall()
        share_recipients = get_db().execute(
            """
            SELECT shares.file_id, users.id AS user_id, users.username
            FROM shares
            JOIN files ON files.id = shares.file_id
            JOIN users ON users.id = shares.user_id
            WHERE files.owner_id = ?
            ORDER BY users.username
            """,
            (g.user["id"],),
        ).fetchall()
        shared_files = get_db().execute(
            """
            SELECT files.*, users.username AS owner_name
            FROM shares
            JOIN files ON files.id = shares.file_id
            JOIN users ON users.id = files.owner_id
            WHERE shares.user_id = ?
            ORDER BY shares.created_at DESC
            """,
            (g.user["id"],),
        ).fetchall()
        users = get_db().execute(
            "SELECT id, username FROM users WHERE id != ? ORDER BY username",
            (g.user["id"],),
        ).fetchall()
        return render_template(
            "dashboard.html",
            owned_files=owned_files,
            share_recipients=share_recipients,
            shared_files=shared_files,
            users=users,
        )

    @app.get("/admin")
    @admin_required
    def admin_panel() -> Any:
        users = get_db().execute(
            """
            SELECT id, username, is_admin, must_change_password, created_at
            FROM users ORDER BY username
            """
        ).fetchall()
        files = get_db().execute(
            """
            SELECT files.*, users.username AS owner_name,
                COUNT(shares.user_id) AS share_count
            FROM files
            JOIN users ON users.id = files.owner_id
            LEFT JOIN shares ON shares.file_id = files.id
            GROUP BY files.id
            ORDER BY files.uploaded_at DESC
            """
        ).fetchall()
        return render_template("admin.html", users=users, files=files)

    @app.post("/admin/users/<int:user_id>/reset-password")
    @admin_required
    def reset_user_password(user_id: int) -> Any:
        if user_id == g.user["id"]:
            flash("Use the change-password page to update your own password.", "warning")
            return redirect(url_for("admin_panel"))
        new_password = request.form.get("new_password", "")
        if len(new_password) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return redirect(url_for("admin_panel"))
        cursor = get_db().execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = 1, session_token = ?
            WHERE id = ?
            """,
            (generate_password_hash(new_password), secrets.token_urlsafe(32), user_id),
        )
        if cursor.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("Password reset. The user must change it after logging in.", "success")
        return redirect(url_for("admin_panel"))

    @app.post("/admin/users/<int:user_id>/make-admin")
    @admin_required
    def make_user_admin(user_id: int) -> Any:
        cursor = get_db().execute(
            "UPDATE users SET is_admin = 1 WHERE id = ?", (user_id,)
        )
        if cursor.rowcount == 0:
            abort(404)
        get_db().commit()
        flash("User is now an administrator.", "success")
        return redirect(url_for("admin_panel"))

    @app.post("/admin/users/<int:user_id>/delete")
    @admin_required
    def admin_delete_user(user_id: int) -> Any:
        if user_id == g.user["id"]:
            flash("You cannot delete your own account.", "error")
            return redirect(url_for("admin_panel"))
        user = get_db().execute(
            "SELECT is_admin FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            abort(404)
        if user["is_admin"]:
            flash("Administrator accounts cannot be deleted here.", "error")
            return redirect(url_for("admin_panel"))
        stored_names = get_db().execute(
            "SELECT stored_name FROM files WHERE owner_id = ?", (user_id,)
        ).fetchall()
        get_db().execute("DELETE FROM users WHERE id = ?", (user_id,))
        get_db().commit()
        for file in stored_names:
            try:
                os.remove(os.path.join(app.config["UPLOAD_FOLDER"], file["stored_name"]))
            except FileNotFoundError:
                pass
        flash("User account and its files have been deleted.", "success")
        return redirect(url_for("admin_panel"))

    @app.post("/upload")
    @login_required
    def upload() -> Any:
        is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        uploads = request.files.getlist("files")
        if not uploads:
            legacy_upload = request.files.get("file")
            uploads = [legacy_upload] if legacy_upload is not None else []
        uploads = [uploaded for uploaded in uploads if uploaded.filename]
        if not uploads:
            if is_xhr:
                return jsonify(error="Choose at least one file."), 400
            flash("Choose a file first.", "error")
            return redirect(url_for("dashboard"))

        if len(uploads) > app.config["MAX_UPLOAD_FILES"]:
            message = (
                f"You can upload no more than {app.config['MAX_UPLOAD_FILES']} "
                f"file(s) at a time."
            )
            if is_xhr:
                return jsonify(error=message), 413
            flash(message, "error")
            return redirect(url_for("dashboard"))

        prepared_uploads = []
        for uploaded in uploads:
            original_name = uploaded.filename.replace("\\", "/").rsplit("/", 1)[-1]
            original_name = "".join(
                character for character in original_name if character.isprintable()
            ).strip()
            if original_name in {"", ".", ".."}:
                if is_xhr:
                    return jsonify(error=f"Invalid file name: {uploaded.filename}."), 400
                flash(f"Invalid file name: {uploaded.filename}.", "error")
                return redirect(url_for("dashboard"))
            prepared_uploads.append((uploaded, original_name, uuid.uuid4().hex))

        visibility = request.form.get("visibility", "private")
        if visibility not in {"private", "public"}:
            abort(400, description="Invalid file visibility.")

        for uploaded, _original_name, stored_name in prepared_uploads:
            uploaded.save(os.path.join(app.config["UPLOAD_FOLDER"], stored_name))
        get_db().executemany(
            """
            INSERT INTO files (owner_id, original_name, stored_name, visibility)
            VALUES (?, ?, ?, ?)
            """,
            [
                (g.user["id"], original_name, stored_name, visibility)
                for _uploaded, original_name, stored_name in prepared_uploads
            ],
        )
        get_db().commit()
        if is_xhr:
            return jsonify(success=True, count=len(prepared_uploads))
        flash(f"{len(prepared_uploads)} file(s) uploaded successfully.", "success")
        return redirect(url_for("dashboard"))

    @app.get("/public")
    def public_files() -> Any:
        files = get_db().execute(
            """
            SELECT files.*, users.username AS owner_name
            FROM files JOIN users ON users.id = files.owner_id
            WHERE files.visibility = 'public'
            ORDER BY files.uploaded_at DESC
            """
        ).fetchall()
        return render_template("public.html", files=files)

    @app.get("/files/<int:file_id>/download")
    def download(file_id: int) -> Any:
        file = get_db().execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if file is None:
            abort(404)
        can_download = file["visibility"] == "public"
        if g.user is not None:
            can_download = can_download or file["owner_id"] == g.user["id"]
            if not can_download:
                shared = get_db().execute(
                    "SELECT 1 FROM shares WHERE file_id = ? AND user_id = ?",
                    (file_id, g.user["id"]),
                ).fetchone()
                can_download = shared is not None
        if not can_download:
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            file["stored_name"],
            as_attachment=True,
            download_name=file["original_name"],
        )

    @app.post("/files/<int:file_id>/visibility")
    @login_required
    def change_visibility(file_id: int) -> Any:
        file = get_db().execute(
            "SELECT owner_id FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if file is None:
            abort(404)
        if file["owner_id"] != g.user["id"] and not g.user["is_admin"]:
            abort(403)
        visibility = request.form.get("visibility")
        if visibility not in {"private", "public"}:
            abort(400, description="Invalid visibility setting.")
        get_db().execute(
            "UPDATE files SET visibility = ? WHERE id = ?", (visibility, file_id)
        )
        get_db().commit()
        flash("File visibility updated.", "success")
        return redirect(url_for("admin_panel") if g.user["is_admin"] else url_for("dashboard"))

    @app.post("/files/<int:file_id>/share")
    @login_required
    def share_file(file_id: int) -> Any:
        file = get_db().execute(
            "SELECT owner_id FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if file is None:
            abort(404)
        if file["owner_id"] != g.user["id"]:
            abort(403)
        try:
            recipient_id = int(request.form.get("user_id", ""))
        except ValueError:
            abort(400, description="Invalid user selection.")
        recipient = get_db().execute(
            "SELECT id FROM users WHERE id = ? AND id != ?",
            (recipient_id, g.user["id"]),
        ).fetchone()
        if recipient is None:
            flash("The selected user could not be found.", "error")
        else:
            get_db().execute(
                "INSERT OR IGNORE INTO shares (file_id, user_id) VALUES (?, ?)",
                (file_id, recipient_id),
            )
            get_db().commit()
            flash("File shared with the selected user.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/files/<int:file_id>/share/<int:user_id>/remove")
    @login_required
    def remove_share(file_id: int, user_id: int) -> Any:
        file = get_db().execute(
            "SELECT owner_id FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if file is None:
            abort(404)
        if file["owner_id"] != g.user["id"]:
            abort(403)
        get_db().execute(
            "DELETE FROM shares WHERE file_id = ? AND user_id = ?",
            (file_id, user_id),
        )
        get_db().commit()
        flash("The user's access has been revoked.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/files/<int:file_id>/delete")
    @login_required
    def delete_file(file_id: int) -> Any:
        file = get_db().execute(
            "SELECT owner_id, stored_name FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if file is None:
            abort(404)
        if file["owner_id"] != g.user["id"] and not g.user["is_admin"]:
            abort(403)
        get_db().execute("DELETE FROM files WHERE id = ?", (file_id,))
        get_db().commit()
        try:
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], file["stored_name"]))
        except FileNotFoundError:
            pass
        flash("File deleted.", "success")
        return redirect(url_for("admin_panel") if g.user["is_admin"] else url_for("dashboard"))

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error: RequestEntityTooLarge) -> tuple[str, int]:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(error="The upload request exceeds the allowed size."), 413
        return render_template("error.html", message="The upload request exceeds the allowed size."), 413

    @app.errorhandler(400)
    def bad_request(error: Any) -> tuple[str, int]:
        return render_template("error.html", message=error.description), 400

    @app.errorhandler(403)
    def forbidden(_error: Any) -> tuple[str, int]:
        return render_template("error.html", message="You do not have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(_error: Any) -> tuple[str, int]:
        return render_template("error.html", message="This file or page could not be found."), 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("K1BOX_HOST", "0.0.0.0"),
        port=int(os.environ.get("K1BOX_PORT", "5000")),
        debug=False,
    )
