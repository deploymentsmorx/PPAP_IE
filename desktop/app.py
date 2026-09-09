"""SmorX PPAP desktop client — Activate + Login for licensed EXE installs."""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError as exc:  # pragma: no cover
    raise SystemExit("tkinter is required for the desktop client.") from exc


def api_base() -> str:
    return (os.environ.get("PPAP_API_URL") or "http://127.0.0.1:8010/api").rstrip("/")


def web_root() -> str:
    base = api_base()
    return base[: -len("/api")] if base.endswith("/api") else base


def open_dashboard(token: str) -> None:
    webbrowser.open(f"{web_root()}/?token={token}")


def device_id() -> str:
    """Matches the "Device ID" shown on Windows Settings > System > About."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\SQMClient") as key:
            machine_id, _ = winreg.QueryValueEx(key, "MachineId")
        return f"DEV-{str(machine_id).strip().strip('{}').upper()}"
    except OSError:
        node = uuid.getnode()
        return f"DEV-{node:012X}"


def host_name() -> str:
    return socket.gethostname() or platform.node() or "UNKNOWN-HOST"


def post_json(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base()}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("detail") or detail
        except json.JSONDecodeError:
            pass
        raise RuntimeError(str(detail)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach API at {api_base()}: {exc.reason}") from exc


class SmorXApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SmorX PPAP — Activate")
        self.geometry("520x560")
        self.resizable(False, False)
        self.token = ""
        self._build_activate()

    def _clear(self) -> None:
        for child in self.winfo_children():
            child.destroy()

    def _build_activate(self) -> None:
        self._clear()
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="SmorX PPAP", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Activate this PC with your license").pack(anchor="w", pady=(0, 12))

        self.dev = tk.StringVar(value=device_id())
        self.host = tk.StringVar(value=host_name())
        self.install_password = tk.StringVar()
        self.license_key = tk.StringVar()
        self.email = tk.StringVar()
        self.temp_password = tk.StringVar()

        for label, var, show in [
            ("Device ID", self.dev, ""),
            ("Host name", self.host, ""),
            ("Install password", self.install_password, "*"),
            ("License key", self.license_key, ""),
            ("Email", self.email, ""),
            ("Temporary password", self.temp_password, "*"),
        ]:
            ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 0))
            state = "readonly" if label in {"Device ID", "Host name"} else "normal"
            ttk.Entry(frame, textvariable=var, show=show, state=state).pack(fill="x")

        ttk.Button(frame, text="Activate", command=self.activate).pack(fill="x", pady=16)
        ttk.Button(frame, text="Already activated? Login", command=self._build_login).pack(fill="x")
        ttk.Label(frame, text=f"API: {api_base()}", foreground="#666").pack(anchor="w", pady=(16, 0))

    def _build_login(self) -> None:
        self._clear()
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Login", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.login_email = tk.StringVar()
        self.login_password = tk.StringVar()
        for label, var, show in [
            ("Email", self.login_email, ""),
            ("Password", self.login_password, "*"),
        ]:
            ttk.Label(frame, text=label).pack(anchor="w", pady=(8, 0))
            ttk.Entry(frame, textvariable=var, show=show).pack(fill="x")
        ttk.Button(frame, text="Sign in", command=self.login).pack(fill="x", pady=16)
        ttk.Button(frame, text="Back to Activate", command=self._build_activate).pack(fill="x")

    def _build_change_password(self) -> None:
        self._clear()
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Change password", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.cur_pw = tk.StringVar()
        self.new_pw = tk.StringVar()
        ttk.Label(frame, text="Current / temporary password").pack(anchor="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.cur_pw, show="*").pack(fill="x")
        ttk.Label(frame, text="New password (min 8)").pack(anchor="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.new_pw, show="*").pack(fill="x")
        ttk.Button(frame, text="Save new password", command=self.change_password).pack(fill="x", pady=16)

    def activate(self) -> None:
        try:
            result = post_json(
                "/license/activate",
                {
                    "license_key": self.license_key.get().strip(),
                    "install_password": self.install_password.get(),
                    "email": self.email.get().strip(),
                    "temporary_password": self.temp_password.get(),
                    "device_id": self.dev.get().strip(),
                    "host_name": self.host.get().strip(),
                },
            )
            messagebox.showinfo("Activated", f"Activated for {result.get('company_name', 'customer')}. You can log in now.")
            self._build_login()
            self.login_email.set(self.email.get().strip())
            self._license_key = self.license_key.get().strip()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Activation failed", str(exc))

    def login(self) -> None:
        try:
            result = post_json(
                "/license/login",
                {
                    "email": self.login_email.get().strip(),
                    "password": self.login_password.get(),
                    "license_key": getattr(self, "_license_key", ""),
                    "device_id": device_id(),
                    "host_name": host_name(),
                },
            )
            self.token = result.get("token") or ""
            user = result.get("user") or {}
            if user.get("must_change_password"):
                self._build_change_password()
                return
            open_dashboard(self.token)
            self.destroy()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Login failed", str(exc))

    def change_password(self) -> None:
        body = json.dumps(
            {
                "current_password": self.cur_pw.get(),
                "new_password": self.new_pw.get(),
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{api_base()}/auth/change-password",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                json.loads(resp.read().decode("utf-8"))
            open_dashboard(self.token)
            self.destroy()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Change password failed", str(exc))


def main() -> None:
    # Allow bundling a default API URL next to the executable.
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    cfg = base / "api_url.txt"
    if cfg.exists() and not os.environ.get("PPAP_API_URL"):
        os.environ["PPAP_API_URL"] = cfg.read_text(encoding="utf-8").strip()
    app = SmorXApp()
    app.mainloop()


if __name__ == "__main__":
    main()
