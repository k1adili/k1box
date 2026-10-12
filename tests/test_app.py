import io
import os
import sqlite3
import tempfile
import unittest
from urllib.parse import quote

from app import create_app, format_local_datetime


class FileSharingAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": os.path.join(self.temp_dir.name, "test.sqlite3"),
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def csrf_token(self, client=None) -> str:
        with (client or self.client).session_transaction() as session:
            return session["_csrf_token"]

    def create_user(self, username: str, password: str = "password123"):
        client = self.app.test_client()
        client.get("/register")
        response = client.post(
            "/register",
            data={
                "csrf_token": self.csrf_token(client),
                "username": username,
                "password": password,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.app.get_db().execute(
                "UPDATE users SET is_active = 1 WHERE username = ?", (username,)
            )
            self.app.get_db().commit()
        client.get("/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": username,
                "password": password,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/dashboard").status_code, 200)
        return client

    def upload(self, client, name: str, visibility: str):
        return client.post(
            "/upload",
            data={
                "csrf_token": self.csrf_token(client),
                "visibility": visibility,
                "file": (io.BytesIO(b"sample content"), name),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    def upload_many(self, client, files, visibility: str = "private"):
        return client.post(
            "/upload",
            data={
                "csrf_token": self.csrf_token(client),
                "visibility": visibility,
                "files": files,
            },
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

    def file_id(self, name: str) -> int:
        with self.app.app_context():
            row = self.app.get_db().execute(
                "SELECT id FROM files WHERE original_name = ?", (name,)
            ).fetchone()
            return row["id"]

    def user_id(self, username: str) -> int:
        with self.app.app_context():
            row = self.app.get_db().execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()
            return row["id"]

    def logged_in_admin(self):
        client = self.app.test_client()
        client.get("/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "admin",
                "password": "admin",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/dashboard")
        self.assertEqual(client.get("/dashboard").location, "/change-password")
        client.get("/change-password")
        response = client.post(
            "/change-password",
            data={
                "csrf_token": self.csrf_token(client),
                "current_password": "admin",
                "new_password": "admin-password-123",
                "confirm_password": "admin-password-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/admin").status_code, 200)
        return client

    def test_local_datetime_conversion(self) -> None:
        self.assertEqual(format_local_datetime("2024-03-20 09:07:00"), "2024/03/20 12:37")
        self.assertEqual(format_local_datetime("2025-03-21 15:30:00"), "2025/03/21 19:00")
        self.assertEqual(format_local_datetime("2024-03-19 22:00:00"), "2024/03/20 01:30")

    def test_site_uses_english_language_and_ltr_direction(self) -> None:
        response = self.client.get("/public")

        self.assertIn(b'<html lang="en" dir="ltr">', response.data)
        self.assertIn(b"Public files", response.data)

    def test_file_dates_are_english_in_dashboard_and_public_list(self) -> None:
        owner = self.create_user("owner")
        self.upload(owner, "date.txt", "public")
        with self.app.app_context():
            self.app.get_db().execute(
                "UPDATE files SET uploaded_at = ? WHERE original_name = ?",
                ("2024-03-20 09:07:00", "date.txt"),
            )
            self.app.get_db().commit()

        self.assertIn(b"2024/03/20 12:37", owner.get("/dashboard").data)
        self.assertIn(b"2024/03/20 12:37", self.client.get("/public").data)

    def test_public_file_is_listed_and_downloadable_without_login(self) -> None:
        owner = self.create_user("owner")
        self.upload(owner, "public.txt", "public")
        file_id = self.file_id("public.txt")

        anonymous = self.app.test_client()
        listing = anonymous.get("/public")
        download = anonymous.get(f"/files/{file_id}/download")

        self.assertIn(b"public.txt", listing.data)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, b"sample content")
        download.close()

    def test_upload_progress_request_returns_json_success(self) -> None:
        owner = self.create_user("xhr_uploader")
        response = owner.post(
            "/upload",
            data={
                "csrf_token": self.csrf_token(owner),
                "visibility": "private",
                "file": (io.BytesIO(b"sample content"), "xhr.txt"),
            },
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "count": 1})

    def test_upload_has_no_app_configured_request_size_limit(self) -> None:
        self.assertIsNone(self.app.config["MAX_CONTENT_LENGTH"])

    def test_upload_accepts_file_larger_than_previous_limit(self) -> None:
        owner = self.create_user("unlimited_uploader")
        response = owner.post(
            "/upload",
            data={
                "csrf_token": self.csrf_token(owner),
                "visibility": "private",
                "file": (io.BytesIO(b"x" * (1024 * 1024 + 1)), "large.txt"),
            },
            content_type="multipart/form-data",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "count": 1})
        with self.app.app_context():
            self.assertIsNotNone(
                self.app.get_db().execute(
                    "SELECT id FROM files WHERE original_name = ?", ("large.txt",)
                ).fetchone()
            )

    def test_upload_multiple_files_in_one_request(self) -> None:
        owner = self.create_user("multi_uploader")
        response = self.upload_many(
            owner,
            [
                (io.BytesIO(b"first file"), "first.txt"),
                (io.BytesIO(b"second file"), "second.txt"),
            ],
            visibility="public",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "count": 2})
        with self.app.app_context():
            files = self.app.get_db().execute(
                "SELECT original_name, visibility FROM files ORDER BY original_name"
            ).fetchall()
        self.assertEqual(
            [(file["original_name"], file["visibility"]) for file in files],
            [("first.txt", "public"), ("second.txt", "public")],
        )

    def test_upload_rejects_batch_over_file_count_limit(self) -> None:
        owner = self.create_user("batch_limit_user")
        self.app.config["MAX_UPLOAD_FILES"] = 1
        response = self.upload_many(
            owner,
            [
                (io.BytesIO(b"first"), "first.txt"),
                (io.BytesIO(b"second"), "second.txt"),
            ],
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json, {"error": "You can upload no more than 1 file(s) at a time."}
        )
        with self.app.app_context():
            self.assertEqual(
                self.app.get_db().execute("SELECT COUNT(*) FROM files").fetchone()[0],
                0,
            )

    def test_upload_batch_has_no_total_size_limit(self) -> None:
        owner = self.create_user("unlimited_batch_user")
        response = self.upload_many(
            owner,
            [
                (io.BytesIO(b"x" * (600 * 1024)), "first.txt"),
                (io.BytesIO(b"y" * (600 * 1024)), "second.txt"),
            ],
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"success": True, "count": 2})
        with self.app.app_context():
            self.assertEqual(self.app.get_db().execute("SELECT COUNT(*) FROM files").fetchone()[0], 2)

    def test_private_file_requires_owner_or_explicit_share(self) -> None:
        owner = self.create_user("owner")
        recipient = self.create_user("recipient")
        self.upload(owner, "private.txt", "private")
        file_id = self.file_id("private.txt")

        self.assertEqual(recipient.get(f"/files/{file_id}/download").status_code, 404)
        owner.post(
            f"/files/{file_id}/share",
            data={
                "csrf_token": self.csrf_token(owner),
                "user_id": str(self.user_id("recipient")),
            },
            follow_redirects=True,
        )

        response = recipient.get(f"/files/{file_id}/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"sample content")
        response.close()

        owner.post(
            f"/files/{file_id}/share/{self.user_id('recipient')}/remove",
            data={"csrf_token": self.csrf_token(owner)},
            follow_redirects=True,
        )
        self.assertEqual(recipient.get(f"/files/{file_id}/download").status_code, 404)

    def test_persian_filename_is_preserved(self) -> None:
        owner = self.create_user("owner")
        self.upload(owner, "گزارش.txt", "public")
        file_id = self.file_id("گزارش.txt")

        response = self.app.test_client().get(f"/files/{file_id}/download")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"filename*=UTF-8''{quote('گزارش.txt', safe='')}",
            response.headers["Content-Disposition"],
        )
        response.close()

    def test_registration_rejects_weak_password(self) -> None:
        self.client.get("/register")
        response = self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf_token(),
                "username": "newuser",
                "password": "short",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Password must be at least 8 characters long.", response.data)

    def test_new_accounts_wait_for_admin_approval_before_login(self) -> None:
        client = self.app.test_client()
        client.get("/register")
        response = client.post(
            "/register",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "pending_user",
                "password": "password123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/login")
        login_page = client.get("/login")
        self.assertIn(b"waiting for administrator approval", login_page.data)
        with self.app.app_context():
            pending = self.app.get_db().execute(
                "SELECT is_active FROM users WHERE username = ?", ("pending_user",)
            ).fetchone()
            self.assertEqual(pending["is_active"], 0)
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "pending_user",
                "password": "password123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"waiting for administrator approval or has been deactivated", response.data)
        self.assertIn(b'class="notification notification-warning"', response.data)
        self.assertIn(b"Action required", response.data)
        self.assertIn(b"Dismiss notification", response.data)
        self.assertEqual(client.get("/dashboard").location, "/login")

        admin = self.logged_in_admin()
        pending_id = self.user_id("pending_user")
        response = admin.post(
            f"/admin/users/{pending_id}/toggle-active",
            data={"csrf_token": self.csrf_token(admin)},
        )

        self.assertEqual(response.status_code, 302)
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "pending_user",
                "password": "password123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/dashboard").status_code, 200)

    def test_admin_can_deactivate_user_and_revoke_existing_session(self) -> None:
        user = self.create_user("deactivate_target")
        admin = self.logged_in_admin()
        user_id = self.user_id("deactivate_target")

        response = admin.post(
            f"/admin/users/{user_id}/toggle-active",
            data={"csrf_token": self.csrf_token(admin)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(user.get("/dashboard").location, "/login")
        user.get("/login")
        response = user.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(user),
                "username": "deactivate_target",
                "password": "password123",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"waiting for administrator approval or has been deactivated", response.data)

    def test_admin_cannot_deactivate_own_account(self) -> None:
        admin = self.logged_in_admin()
        admin_id = self.user_id("admin")

        response = admin.post(
            f"/admin/users/{admin_id}/toggle-active",
            data={"csrf_token": self.csrf_token(admin)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(admin.get("/admin").status_code, 200)
        with self.app.app_context():
            admin_row = self.app.get_db().execute(
                "SELECT is_active FROM users WHERE id = ?", (admin_id,)
            ).fetchone()
            self.assertEqual(admin_row["is_active"], 1)

    def test_admin_default_password_requires_change_before_using_site(self) -> None:
        client = self.app.test_client()
        client.get("/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "admin",
                "password": "admin",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/admin").location, "/change-password")
        client.get("/change-password")
        response = client.post(
            "/change-password",
            data={
                "csrf_token": self.csrf_token(client),
                "current_password": "admin",
                "new_password": "secure-admin-password",
                "confirm_password": "secure-admin-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get("/admin").status_code, 200)

    def test_non_admin_cannot_access_admin_panel(self) -> None:
        user = self.create_user("regular_user")

        self.assertEqual(user.get("/admin").status_code, 403)

    def test_admin_can_promote_user_to_administrator(self) -> None:
        user = self.create_user("promoted_user")
        admin = self.logged_in_admin()
        user_id = self.user_id("promoted_user")

        response = admin.post(
            f"/admin/users/{user_id}/make-admin",
            data={"csrf_token": self.csrf_token(admin)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin")
        self.assertEqual(user.get("/admin").status_code, 200)
        self.assertIn(b"Admin panel", user.get("/dashboard").data)
        with self.app.app_context():
            promoted_user = self.app.get_db().execute(
                "SELECT is_admin FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            self.assertEqual(promoted_user["is_admin"], 1)

    def test_non_admin_cannot_promote_users(self) -> None:
        self.create_user("candidate_user")
        requester = self.create_user("regular_user")

        response = requester.post(
            f"/admin/users/{self.user_id('candidate_user')}/make-admin",
            data={"csrf_token": self.csrf_token(requester)},
        )

        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            candidate = self.app.get_db().execute(
                "SELECT is_admin FROM users WHERE username = ?",
                ("candidate_user",),
            ).fetchone()
            self.assertEqual(candidate["is_admin"], 0)

    def test_admin_can_reset_user_password_and_user_must_change_it(self) -> None:
        previous_session = self.create_user("reset_target")
        admin = self.logged_in_admin()
        response = admin.post(
            f"/admin/users/{self.user_id('reset_target')}/reset-password",
            data={
                "csrf_token": self.csrf_token(admin),
                "new_password": "temporary-password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(previous_session.get("/dashboard").location, "/login")
        user = self.app.test_client()
        user.get("/login")
        response = user.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(user),
                "username": "reset_target",
                "password": "temporary-password",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(user.get("/dashboard").location, "/change-password")

    def test_admin_can_change_visibility_and_delete_other_users_files(self) -> None:
        owner = self.create_user("file_owner")
        self.upload(owner, "managed.txt", "private")
        file_id = self.file_id("managed.txt")
        with self.app.app_context():
            stored_name = self.app.get_db().execute(
                "SELECT stored_name FROM files WHERE id = ?", (file_id,)
            ).fetchone()["stored_name"]
        stored_path = os.path.join(self.temp_dir.name, "uploads", stored_name)
        self.assertTrue(os.path.exists(stored_path))
        admin = self.logged_in_admin()

        response = admin.post(
            f"/files/{file_id}/visibility",
            data={
                "csrf_token": self.csrf_token(admin),
                "visibility": "public",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin")
        self.assertIn(b"managed.txt", self.client.get("/public").data)

        response = admin.post(
            f"/files/{file_id}/delete",
            data={"csrf_token": self.csrf_token(admin)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/admin")
        self.assertFalse(os.path.exists(stored_path))
        with self.app.app_context():
            self.assertIsNone(
                self.app.get_db().execute(
                    "SELECT id FROM files WHERE id = ?", (file_id,)
                ).fetchone()
            )

    def test_admin_can_delete_user_and_their_uploaded_files(self) -> None:
        self.create_user("delete_target")
        owner = self.app.test_client()
        owner.get("/login")
        owner.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(owner),
                "username": "delete_target",
                "password": "password123",
            },
        )
        owner.get("/dashboard")
        self.upload(owner, "account-file.txt", "private")
        file_id = self.file_id("account-file.txt")
        with self.app.app_context():
            stored_name = self.app.get_db().execute(
                "SELECT stored_name FROM files WHERE id = ?", (file_id,)
            ).fetchone()["stored_name"]
        stored_path = os.path.join(self.temp_dir.name, "uploads", stored_name)
        admin = self.logged_in_admin()

        response = admin.post(
            f"/admin/users/{self.user_id('delete_target')}/delete",
            data={"csrf_token": self.csrf_token(admin)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(os.path.exists(stored_path))
        with self.app.app_context():
            self.assertIsNone(
                self.app.get_db().execute(
                    "SELECT id FROM users WHERE username = ?", ("delete_target",)
                ).fetchone()
            )
            self.assertIsNone(
                self.app.get_db().execute(
                    "SELECT id FROM files WHERE id = ?", (file_id,)
                ).fetchone()
            )

    def test_existing_database_is_migrated_without_losing_accounts(self) -> None:
        database_path = os.path.join(self.temp_dir.name, "legacy.sqlite3")
        database = sqlite3.connect(database_path)
        try:
            database.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            database.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("legacy_user", "existing-password-hash"),
            )
            database.commit()
        finally:
            database.close()

        migrated_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": database_path,
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "legacy-uploads"),
            }
        )

        with migrated_app.app_context():
            users = migrated_app.get_db().execute(
                "SELECT username, is_admin, is_active, must_change_password "
                "FROM users ORDER BY username"
            ).fetchall()
        self.assertEqual(
            [
                (
                    user["username"],
                    user["is_admin"],
                    user["is_active"],
                    user["must_change_password"],
                )
                for user in users
            ],
            [("admin", 1, 1, 1), ("legacy_user", 0, 1, 0)],
        )

    def test_admin_account_survives_restart_without_resetting_changed_password(self) -> None:
        admin = self.logged_in_admin()
        restarted_app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key",
                "DATABASE": os.path.join(self.temp_dir.name, "test.sqlite3"),
                "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            }
        )
        client = restarted_app.test_client()
        client.get("/login")
        response = client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "admin",
                "password": "admin-password-123",
            },
        )

        self.assertEqual(response.status_code, 302)
        with restarted_app.app_context():
            user = restarted_app.get_db().execute(
                "SELECT is_admin, must_change_password FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()
            self.assertEqual(user["is_admin"], 1)
            self.assertEqual(user["must_change_password"], 0)


if __name__ == "__main__":
    unittest.main()
