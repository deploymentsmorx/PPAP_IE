# Licensed EXE customers and engineers (Super Admin).

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from .auth import (
    PLATFORM_ROLES,
    ROLE_FULL_ACCESS,
    ROLE_QUALITY,
    ROLE_SUPER_ADMIN,
    hash_password,
    role_label,
    serialize_user,
    verify_password,
)
from .db import DbConnection


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_role(value: Any, grant_full_access: bool = False) -> str:
    role = str(value or "").strip()
    if role in PLATFORM_ROLES:
        return role
    return ROLE_FULL_ACCESS if grant_full_access else ROLE_QUALITY


def generate_license_key(conn: DbConnection, year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    for _ in range(40):
        suffix = secrets.token_hex(2).upper()
        key = f"AB-{year}-{suffix}"
        existing = conn.execute(
            "SELECT 1 AS ok FROM customers WHERE license_key = ?",
            (key,),
        ).fetchone()
        if existing is None:
            return key
    return f"AB-{year}-{secrets.token_hex(3).upper()}"


def create_customer(conn: DbConnection, payload: dict[str, Any]) -> dict[str, Any]:
    company = str(payload.get("company_name") or "").strip()
    if len(company) < 2:
        raise ValueError("Company / organization name is required.")

    install_password = str(payload.get("install_password") or "").strip()
    if len(install_password) < 6:
        raise ValueError("Install password must be at least 6 characters.")

    license_key = str(payload.get("license_key") or "").strip() or generate_license_key(conn)
    device_id = str(payload.get("device_id") or "").strip()
    host_name = str(payload.get("host_name") or "").strip()
    require_device = 1 if payload.get("require_device", True) else 0

    engineer_name = str(payload.get("engineer_full_name") or "").strip()
    engineer_email = str(payload.get("engineer_email") or "").strip().lower()
    temp_password = str(payload.get("temporary_password") or "").strip()
    role = _normalize_role(payload.get("role"), grant_full_access=bool(payload.get("grant_full_access", True)))
    grant_full_access = 1 if role in {ROLE_SUPER_ADMIN, ROLE_FULL_ACCESS} else 0

    if not engineer_name or not engineer_email or len(temp_password) < 6:
        raise ValueError("Engineer full name, email, and temporary password (min 6) are required.")

    customer_id = secrets.token_hex(8)
    engineer_id = secrets.token_hex(8)
    now = _utc_now()
    install_salt, install_hash = hash_password(install_password)
    eng_salt, eng_hash = hash_password(temp_password)

    conn.execute(
        """
        INSERT INTO customers (
            customer_id, company_name, license_key, install_password_salt, install_password_hash,
            device_id, host_name, require_device, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            customer_id,
            company,
            license_key,
            install_salt,
            install_hash,
            device_id,
            host_name,
            require_device,
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO customer_engineers (
            engineer_id, customer_id, full_name, email, password_salt, password_hash,
            device_id, host_name, grant_full_access, role, must_change_password, activated, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        """,
        (
            engineer_id,
            customer_id,
            engineer_name,
            engineer_email,
            eng_salt,
            eng_hash,
            device_id,
            host_name,
            grant_full_access,
            role,
            now,
        ),
    )
    conn.commit()
    return {
        **serialize_customer(conn, customer_id),
        "credentials": {
            "license_key": license_key,
            "install_password": install_password,
            "email": engineer_email,
            "temporary_password": temp_password,
        },
    }


def add_engineer(conn: DbConnection, customer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    customer = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    if customer is None:
        raise KeyError("Customer not found.")

    full_name = str(payload.get("full_name") or payload.get("engineer_full_name") or "").strip()
    email = str(payload.get("email") or payload.get("engineer_email") or "").strip().lower()
    temp_password = str(payload.get("temporary_password") or "").strip()
    device_id = str(payload.get("device_id") or "").strip()
    host_name = str(payload.get("host_name") or "").strip()
    role = _normalize_role(payload.get("role"), grant_full_access=bool(payload.get("grant_full_access", True)))
    grant_full_access = 1 if role in {ROLE_SUPER_ADMIN, ROLE_FULL_ACCESS} else 0

    if not full_name or not email or len(temp_password) < 6:
        raise ValueError("Name, email, and temporary password (min 6) are required.")

    salt, password_hash = hash_password(temp_password)
    engineer_id = secrets.token_hex(8)
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO customer_engineers (
            engineer_id, customer_id, full_name, email, password_salt, password_hash,
            device_id, host_name, grant_full_access, role, must_change_password, activated, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        """,
        (
            engineer_id,
            customer_id,
            full_name,
            email,
            salt,
            password_hash,
            device_id,
            host_name,
            grant_full_access,
            role,
            now,
        ),
    )
    conn.commit()
    return {
        "engineer": serialize_engineer(
            conn.execute(
                "SELECT * FROM customer_engineers WHERE engineer_id = ?",
                (engineer_id,),
            ).fetchone()
        ),
        "credentials": {
            "license_key": customer["license_key"],
            "email": email,
            "temporary_password": temp_password,
            "device_id": device_id,
            "host_name": host_name,
        },
    }


def list_customers(conn: DbConnection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM customers ORDER BY created_at DESC"
    ).fetchall()
    items = []
    for row in rows:
        engineers = conn.execute(
            "SELECT * FROM customer_engineers WHERE customer_id = ? ORDER BY created_at",
            (row["customer_id"],),
        ).fetchall()
        items.append(
            {
                **_customer_public(row),
                "engineers": [serialize_engineer(e) for e in engineers],
                "engineer_count": len(engineers),
            }
        )
    return {"items": items, "count": len(items)}


def serialize_customer(conn: DbConnection, customer_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    if row is None:
        raise KeyError("Customer not found.")
    engineers = conn.execute(
        "SELECT * FROM customer_engineers WHERE customer_id = ? ORDER BY created_at",
        (customer_id,),
    ).fetchall()
    return {
        **_customer_public(row),
        "engineers": [serialize_engineer(e) for e in engineers],
    }


def _customer_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer_id": row["customer_id"],
        "company_name": row["company_name"],
        "license_key": row["license_key"],
        "device_id": row.get("device_id") or "",
        "host_name": row.get("host_name") or "",
        "require_device": bool(row.get("require_device")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def serialize_engineer(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    role = _normalize_role(row.get("role"), grant_full_access=bool(row.get("grant_full_access")))
    return {
        "engineer_id": row["engineer_id"],
        "customer_id": row["customer_id"],
        "full_name": row["full_name"],
        "email": row["email"],
        "device_id": row.get("device_id") or "",
        "host_name": row.get("host_name") or "",
        "role": role,
        "role_label": role_label(role),
        "grant_full_access": bool(row.get("grant_full_access")),
        "must_change_password": bool(row.get("must_change_password")),
        "activated": bool(row.get("activated")),
        "created_at": row.get("created_at"),
    }


def activate_license(conn: DbConnection, payload: dict[str, Any]) -> dict[str, Any]:
    license_key = str(payload.get("license_key") or "").strip()
    install_password = str(payload.get("install_password") or "")
    email = str(payload.get("email") or "").strip().lower()
    temporary_password = str(payload.get("temporary_password") or "")
    device_id = str(payload.get("device_id") or "").strip()
    host_name = str(payload.get("host_name") or "").strip()

    if not all([license_key, install_password, email, temporary_password]):
        raise ValueError("License key, install password, email, and temporary password are required.")

    customer = conn.execute(
        "SELECT * FROM customers WHERE license_key = ?",
        (license_key,),
    ).fetchone()
    if customer is None:
        raise PermissionError("Invalid license key or install password.")
    if not verify_password(
        install_password,
        customer["install_password_salt"],
        customer["install_password_hash"],
    ):
        raise PermissionError("Invalid license key or install password.")

    if customer.get("require_device"):
        approved_device = (customer.get("device_id") or "").strip()
        approved_host = (customer.get("host_name") or "").strip()
        eng = conn.execute(
            "SELECT * FROM customer_engineers WHERE customer_id = ? AND LOWER(email) = ?",
            (customer["customer_id"], email),
        ).fetchone()
        eng_device = (eng.get("device_id") if eng else "") or approved_device
        eng_host = (eng.get("host_name") if eng else "") or approved_host
        if eng_device and device_id and eng_device != device_id:
            raise PermissionError("This license is locked to a different Device ID.")
        if eng_host and host_name and eng_host.lower() != host_name.lower():
            raise PermissionError("This license is locked to a different host name.")

    engineer = conn.execute(
        "SELECT * FROM customer_engineers WHERE customer_id = ? AND LOWER(email) = ?",
        (customer["customer_id"], email),
    ).fetchone()
    if engineer is None or not verify_password(
        temporary_password,
        engineer["password_salt"],
        engineer["password_hash"],
    ):
        raise PermissionError("Invalid engineer email or temporary password.")

    conn.execute(
        """
        UPDATE customer_engineers
        SET activated = 1,
            device_id = CASE WHEN ? = '' THEN device_id ELSE ? END,
            host_name = CASE WHEN ? = '' THEN host_name ELSE ? END
        WHERE engineer_id = ?
        """,
        (device_id, device_id, host_name, host_name, engineer["engineer_id"]),
    )
    if device_id or host_name:
        conn.execute(
            """
            UPDATE customers
            SET device_id = CASE WHEN device_id = '' AND ? != '' THEN ? ELSE device_id END,
                host_name = CASE WHEN host_name = '' AND ? != '' THEN ? ELSE host_name END,
                updated_at = ?
            WHERE customer_id = ?
            """,
            (device_id, device_id, host_name, host_name, _utc_now(), customer["customer_id"]),
        )
    conn.commit()
    return {
        "status": "activated",
        "company_name": customer["company_name"],
        "license_key": license_key,
        "engineer": serialize_engineer(
            conn.execute(
                "SELECT * FROM customer_engineers WHERE engineer_id = ?",
                (engineer["engineer_id"],),
            ).fetchone()
        ),
    }


def login_engineer(conn: DbConnection, payload: dict[str, Any]) -> dict[str, Any]:
    email = str(payload.get("email") or payload.get("username") or "").strip().lower()
    password = str(payload.get("password") or "")
    device_id = str(payload.get("device_id") or "").strip()
    host_name = str(payload.get("host_name") or "").strip()
    license_key = str(payload.get("license_key") or "").strip()

    if not email or not password:
        raise ValueError("Email and password are required.")

    clauses = ["LOWER(e.email) = ?"]
    params: list[Any] = [email]
    if license_key:
        clauses.append("c.license_key = ?")
        params.append(license_key)

    row = conn.execute(
        f"""
        SELECT e.*, c.license_key, c.company_name, c.require_device,
               c.device_id AS customer_device_id, c.host_name AS customer_host_name
        FROM customer_engineers e
        JOIN customers c ON c.customer_id = e.customer_id
        WHERE {' AND '.join(clauses)}
        """,
        params,
    ).fetchone()
    if row is None or not verify_password(password, row["password_salt"], row["password_hash"]):
        raise PermissionError("Invalid email or password.")
    if not row.get("activated"):
        raise PermissionError("License is not activated on this device yet.")

    if row.get("require_device"):
        approved_device = (row.get("device_id") or row.get("customer_device_id") or "").strip()
        approved_host = (row.get("host_name") or row.get("customer_host_name") or "").strip()
        if approved_device and device_id and approved_device != device_id:
            raise PermissionError("Login blocked: Device ID does not match the licensed PC.")
        if approved_host and host_name and approved_host.lower() != host_name.lower():
            raise PermissionError("Login blocked: Host name does not match the licensed PC.")

    role = _normalize_role(row.get("role"), grant_full_access=bool(row.get("grant_full_access")))
    # Mirror into users table for session tokens.
    user_id = f"eng-{row['engineer_id']}"
    existing = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO users (
                user_id, username, display_name, role, password_salt, password_hash,
                created_at, must_change_password, customer_id, user_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'engineer')
            """,
            (
                user_id,
                email,
                row["full_name"],
                role,
                row["password_salt"],
                row["password_hash"],
                _utc_now(),
                1 if row.get("must_change_password") else 0,
                row["customer_id"],
            ),
        )
    else:
        conn.execute(
            """
            UPDATE users
            SET display_name = ?, role = ?, password_salt = ?, password_hash = ?,
                must_change_password = ?, customer_id = ?, user_kind = 'engineer'
            WHERE user_id = ?
            """,
            (
                row["full_name"],
                role,
                row["password_salt"],
                row["password_hash"],
                1 if row.get("must_change_password") else 0,
                row["customer_id"],
                user_id,
            ),
        )

    token = secrets.token_urlsafe(32)
    now = _utc_now()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now, now),
    )
    conn.commit()
    user_row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    user = serialize_user(dict(user_row))
    user["must_change_password"] = bool(row.get("must_change_password"))
    user["company_name"] = row.get("company_name")
    user["license_key"] = row.get("license_key")
    return {"token": token, "user": user}


def change_engineer_password(
    conn: DbConnection,
    user: dict[str, Any],
    current_password: str,
    new_password: str,
) -> dict[str, Any]:
    if len(new_password or "") < 8:
        raise ValueError("New password must be at least 8 characters.")
    user_id = user["user_id"]
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None or not verify_password(current_password, row["password_salt"], row["password_hash"]):
        raise PermissionError("Current password is incorrect.")

    salt, password_hash = hash_password(new_password)
    conn.execute(
        """
        UPDATE users
        SET password_salt = ?, password_hash = ?, must_change_password = 0
        WHERE user_id = ?
        """,
        (salt, password_hash, user_id),
    )
    if str(user_id).startswith("eng-"):
        engineer_id = str(user_id).removeprefix("eng-")
        conn.execute(
            """
            UPDATE customer_engineers
            SET password_salt = ?, password_hash = ?, must_change_password = 0
            WHERE engineer_id = ?
            """,
            (salt, password_hash, engineer_id),
        )
    conn.commit()
    return {"status": "ok", "must_change_password": False}
