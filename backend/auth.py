# Simple session auth with role-based access.

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from .db import DbConnection
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

PLATFORM_ROLES = {
    ROLE_SUPER_ADMIN,
    ROLE_FULL_ACCESS,
    ROLE_CREATOR,
    ROLE_QUALITY,
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


def ensure_auth_tables(conn: DbConnection) -> None:
    # Tables are created in database.init_db schema; seed defaults here.
    seed_default_users(conn)


def seed_default_users(conn: DbConnection) -> None:
    load_project_env()
    # Only Super Admin signs in directly to the web app (see authenticate()); every
    # other role logs in through the desktop app's device-bound license flow, so no
    # other platform-level default accounts are seeded here.
    defaults = [
        {
            "username": os.getenv("PPAP_SUPER_ADMIN_USER", "superadmin").strip() or "superadmin",
            "display_name": "Super Admin",
            "role": ROLE_SUPER_ADMIN,
            "password": os.getenv("PPAP_SUPER_ADMIN_PASSWORD", "SuperAdmin@123"),
        },
    ]

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
                SET display_name = ?, role = ?, password_salt = ?, password_hash = ?,
                    user_kind = 'platform'
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
                user_id, username, display_name, role, password_salt, password_hash,
                created_at, must_change_password, user_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'platform')
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


def authenticate(conn: DbConnection, username: str, password: str) -> dict[str, Any]:
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
    if row["role"] != ROLE_SUPER_ADMIN:
        raise PermissionError(
            "Only Super Admin can sign in here. Sign in through the SmorX PPAP desktop app instead."
        )

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


def logout(conn: DbConnection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def current_user(conn: DbConnection, token: str | None) -> dict[str, Any] | None:
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


def require_user(conn: DbConnection, token: str | None) -> dict[str, Any]:
    user = current_user(conn, token)
    if user is None:
        raise PermissionError("Authentication required.")
    return user


def require_super_admin(conn: DbConnection, token: str | None) -> dict[str, Any]:
    user = require_user(conn, token)
    if user.get("role") != ROLE_SUPER_ADMIN:
        raise PermissionError("Super admin access required.")
    return user


def permissions_for_role(role: str) -> dict[str, bool]:
    normalized = ROLE_QUALITY if role == ROLE_USER else role
    create = normalized in {ROLE_SUPER_ADMIN, ROLE_FULL_ACCESS, ROLE_CREATOR}
    quality = normalized in {ROLE_SUPER_ADMIN, ROLE_FULL_ACCESS, ROLE_QUALITY}
    rules = normalized == ROLE_SUPER_ADMIN
    admin = normalized == ROLE_SUPER_ADMIN
    return {
        "create": create,
        "quality": quality,
        "rules": rules,
        "admin": admin,
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
        "must_change_password": bool(row.get("must_change_password")),
        "user_kind": row.get("user_kind") or "platform",
        "customer_id": row.get("customer_id"),
        "permissions": permissions,
    }


def list_platform_users(conn: DbConnection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT * FROM users
        WHERE COALESCE(user_kind, 'platform') = 'platform'
        ORDER BY created_at
        """
    ).fetchall()
    return {"items": [serialize_user(dict(row)) for row in rows], "count": len(rows)}


def create_platform_user(conn: DbConnection, payload: dict[str, Any]) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    display_name = str(payload.get("display_name") or username).strip()
    role = str(payload.get("role") or ROLE_QUALITY).strip()
    password = str(payload.get("password") or "").strip()
    if not username or len(password) < 6:
        raise ValueError("Username and password (min 6) are required.")
    if role not in PLATFORM_ROLES:
        raise ValueError("Invalid role.")
    existing = conn.execute("SELECT 1 AS ok FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        raise ValueError("Username already exists.")
    salt, password_hash = hash_password(password)
    user_id = secrets.token_hex(8)
    conn.execute(
        """
        INSERT INTO users (
            user_id, username, display_name, role, password_salt, password_hash,
            created_at, must_change_password, user_kind
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'platform')
        """,
        (
            user_id,
            username,
            display_name,
            role,
            salt,
            password_hash,
            _utc_now(),
            1 if payload.get("must_change_password") else 0,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return serialize_user(dict(row))


def update_platform_user(conn: DbConnection, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ? AND COALESCE(user_kind, 'platform') = 'platform'",
        (user_id,),
    ).fetchone()
    if row is None:
        raise KeyError("User not found.")
    display_name = payload.get("display_name")
    role = payload.get("role")
    password = payload.get("password")
    fields = []
    params: list[Any] = []
    if display_name is not None:
        fields.append("display_name = ?")
        params.append(str(display_name).strip())
    if role is not None:
        if role not in PLATFORM_ROLES:
            raise ValueError("Invalid role.")
        fields.append("role = ?")
        params.append(role)
    if password:
        if len(str(password)) < 6:
            raise ValueError("Password must be at least 6 characters.")
        salt, password_hash = hash_password(str(password))
        fields.extend(["password_salt = ?", "password_hash = ?"])
        params.extend([salt, password_hash])
    if not fields:
        raise ValueError("No updatable fields provided.")
    params.append(user_id)
    conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?", params)
    conn.commit()
    updated = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return serialize_user(dict(updated))


def delete_platform_user(conn: DbConnection, user_id: str) -> None:
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ? AND COALESCE(user_kind, 'platform') = 'platform'",
        (user_id,),
    ).fetchone()
    if row is None:
        raise KeyError("User not found.")
    if row["role"] == ROLE_SUPER_ADMIN:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role = ? AND COALESCE(user_kind, 'platform') = 'platform'",
            (ROLE_SUPER_ADMIN,),
        ).fetchone()
        if int(count["c"]) <= 1:
            raise ValueError("Cannot delete the last super admin.")
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()


def extract_bearer_token(authorization: str | None) -> str | None:
    value = str(authorization or "").strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return value
