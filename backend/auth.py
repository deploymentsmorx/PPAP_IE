# Simple session auth with role-based access.

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .env import load_project_env

ROLE_SUPER_ADMIN = "super_admin"
ROLE_FULL_ACCESS = "full_access"
ROLE_CREATOR = "creator"
ROLE_QUALITY = "quality"
# Legacy role kept for older rows.
ROLE_USER = "user"

VALID_ROLES = {
    ROLE_SUPER_ADMIN,
    ROLE_FULL_ACCESS,
    ROLE_CREATOR,
    ROLE_QUALITY,
    ROLE_USER,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return salt, digest.hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    _, candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def ensure_auth_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        """
    )
    seed_default_users(conn)


def seed_default_users(conn: sqlite3.Connection) -> None:
    load_project_env()
    # Keep passwords in sync with defaults so demo logins stay reliable.
    defaults = [
        {
            "username": os.getenv("PPAP_SUPER_ADMIN_USER", "superadmin").strip() or "superadmin",
            "display_name": "Super Admin",
            "role": ROLE_SUPER_ADMIN,
            "password": os.getenv("PPAP_SUPER_ADMIN_PASSWORD", "SuperAdmin@123"),
        },
        {
            "username": os.getenv("PPAP_FULL_ACCESS_USER", "fullaccess").strip() or "fullaccess",
            "display_name": "Full Access",
            "role": ROLE_FULL_ACCESS,
            "password": os.getenv("PPAP_FULL_ACCESS_PASSWORD", "FullAccess@123"),
        },
        {
            "username": os.getenv("PPAP_CREATOR_USER", "creator").strip() or "creator",
            "display_name": "PPAP Creation",
            "role": ROLE_CREATOR,
            "password": os.getenv("PPAP_CREATOR_PASSWORD", "Creator@123"),
        },
        {
            "username": os.getenv("PPAP_QUALITY_USER", "quality").strip() or "quality",
            "display_name": "Quality Engineer",
            "role": ROLE_QUALITY,
            "password": os.getenv("PPAP_QUALITY_PASSWORD", "Quality@123"),
        },
    ]

    # Migrate legacy generic "user" rows to quality.
    conn.execute(
        "UPDATE users SET role = ? WHERE role = ?",
        (ROLE_QUALITY, ROLE_USER),
    )

    for item in defaults:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE username = ?",
            (item["username"],),
        ).fetchone()
        salt, password_hash = hash_password(item["password"])
        if existing:
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, role = ?, password_salt = ?, password_hash = ?
                WHERE username = ?
                """,
                (
                    item["display_name"],
                    item["role"],
                    salt,
                    password_hash,
                    item["username"],
                ),
            )
            continue
        conn.execute(
            """
            INSERT INTO users (
                user_id, username, display_name, role, password_salt, password_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                secrets.token_hex(8),
                item["username"],
                item["display_name"],
                item["role"],
                salt,
                password_hash,
                _utc_now(),
            ),
        )


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict[str, Any]:
    username = str(username or "").strip()
    password = str(password or "")
    if not username or not password:
        raise ValueError("Username and password are required.")

    row = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if row is None or not verify_password(password, row["password_salt"], row["password_hash"]):
        raise PermissionError("Invalid username or password.")

    token = secrets.token_urlsafe(32)
    created = _utc_now()
    conn.execute(
        """
        INSERT INTO sessions (token, user_id, created_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (token, row["user_id"], created, created),
    )
    conn.commit()
    return {
        "token": token,
        "user": serialize_user(dict(row)),
    }


def logout(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def current_user(conn: sqlite3.Connection, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT users.*
        FROM sessions
        JOIN users ON users.user_id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    ).fetchone()
    return serialize_user(dict(row)) if row else None


def require_user(conn: sqlite3.Connection, token: str | None) -> dict[str, Any]:
    user = current_user(conn, token)
    if user is None:
        raise PermissionError("Authentication required.")
    return user


def require_super_admin(conn: sqlite3.Connection, token: str | None) -> dict[str, Any]:
    user = require_user(conn, token)
    if user.get("role") != ROLE_SUPER_ADMIN:
        raise PermissionError("Super admin access required.")
    return user


def permissions_for_role(role: str) -> dict[str, bool]:
    normalized = ROLE_QUALITY if role == ROLE_USER else role
    create = normalized in {ROLE_SUPER_ADMIN, ROLE_FULL_ACCESS, ROLE_CREATOR}
    quality = normalized in {ROLE_SUPER_ADMIN, ROLE_FULL_ACCESS, ROLE_QUALITY}
    rules = normalized == ROLE_SUPER_ADMIN
    return {
        "create": create,
        "quality": quality,
        "rules": rules,
    }


def role_label(role: str) -> str:
    return {
        ROLE_SUPER_ADMIN: "Super Admin",
        ROLE_FULL_ACCESS: "Full Access",
        ROLE_CREATOR: "PPAP Creation",
        ROLE_QUALITY: "Quality",
        ROLE_USER: "Quality",
    }.get(role, role)


def serialize_user(row: dict[str, Any]) -> dict[str, Any]:
    role = row["role"]
    if role == ROLE_USER:
        role = ROLE_QUALITY
    permissions = permissions_for_role(role)
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row.get("display_name") or row["username"],
        "role": role,
        "role_label": role_label(role),
        "is_super_admin": role == ROLE_SUPER_ADMIN,
        "permissions": permissions,
    }


def extract_bearer_token(authorization: str | None) -> str | None:
    value = str(authorization or "").strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return value
