# -*- coding: utf-8 -*-
"""
WhatsApp Web (Unofficial) via Playwright - FINAL

Features:
- Persistent session per session_id (no QR after login)
- Robust QR extraction (multi-selectors + diagnostics)
- Realtime publish to Desk (event='whatsapp_qr')
- Status cache for polling (frappe.cache)
- Backward-compatible APIs:
    - generate_whatsapp_qr_pw(session_id, timeout=60, headless=1, dump_dir=...) -> dict
    - check_qr_status_pw(session_id) -> dict
- Simple API:
    - get_qr_data_url(device_name, headless=1, dump_dir=...) -> str|None
- Optional utility:
    - clear_qr_status_pw(session_id) -> None
"""

from __future__ import annotations
import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Union

# ---- Failsafe env (prevents /root/.cache issues) ----
os.environ.setdefault("HOME", "/home/frappe")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/home/frappe/.cache/ms-playwright")

try:
    import frappe  # type: ignore
except Exception:
    frappe = None  # allow CLI

# ---------------- Tunables ----------------
WHATSAPP_WEB_URL = "https://web.whatsapp.com/"

QR_SELECTORS = [
    'div[data-testid="qrcode"] canvas',
    'canvas[aria-label="Scan me!"]',
    'div[data-ref] canvas',
    'img[alt="Scan me!"]',
    'div[data-testid="qrcode"] img',
]

LOGIN_MARKERS = [
    'div[data-testid="chat-list-search"]',
    'div[aria-label="Chat list"]',
    'header[data-testid="chatlist-header"]',
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_DUMP_DIR = "/tmp/whatsapp_diag"

# --------------- Cache helpers ---------------
def _cache_key(session_id: str) -> str:
    return f"wa_qr_status::{session_id}"

def _cache_set(session_id: str, payload: dict, ttl: int = 300) -> None:
    if not frappe:  # no-op in CLI mode
        return
    try:
        frappe.cache().set_value(_cache_key(session_id), payload, expires_in_sec=ttl)
    except Exception:
        pass

def _cache_get(session_id: str) -> Optional[dict]:
    if not frappe:
        return None
    try:
        return frappe.cache().get_value(_cache_key(session_id))
    except Exception:
        return None

def _cache_clear(session_id: str) -> None:
    if not frappe:
        return
    try:
        frappe.cache().delete_value(_cache_key(session_id))
    except Exception:
        pass

# --------------- Paths ---------------
def _session_dir(session_id: str) -> str:
    if frappe:
        base = frappe.get_site_path("private", "files", "whatsapp_sessions")
    else:
        base = "/tmp/whatsapp_sessions"
    p = Path(base) / session_id
    p.mkdir(parents=True, exist_ok=True)
    return str(p)

# --------------- Small helpers ---------------
async def _safe_write_text(path: Union[str, Path], text: str) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

async def _append_line(path: Union[str, Path], line: str) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")

def _log_error(title: str, detail: str) -> None:
    title = (title or "Error")[:140]
    if not frappe:
        sys.stderr.write(f"[ERROR] {title}\n{detail}\n")
        return
    try:
        frappe.log_error(title, detail)
    except Exception:
        sys.stderr.write(f"[FRAPPE LOG ERROR FAILED] {title}\n{detail}\n")

def _publish_qr_event(session_id: str, status: str, *, b64: Optional[str] = None, diag: Optional[str] = None) -> None:
    if not frappe:
        return
    try:
        frappe.publish_realtime(
            event="whatsapp_qr",
            message={"device": session_id, "status": status, "qr": b64, "diag": diag},
            user=frappe.session.user,
        )
    except Exception as e:
        _log_error("Realtime publish failed", str(e))

def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return asyncio.run(coro)
    return asyncio.run(coro)

# --------------- Playwright core ---------------
async def _is_logged_in(page) -> bool:
    for marker in LOGIN_MARKERS:
        with contextlib.suppress(Exception):
            if await page.locator(marker).first.is_visible():
                return True
    return False

async def _try_extract_qr_dataurl(page, selector: str) -> Optional[str]:
    elt = page.locator(selector).first
    if not await elt.is_visible():
        return None
    try:
        b64 = await page.evaluate(
            """
            (sel) => {
              const el = document.querySelector(sel);
              if (!el) return null;
              if (el.tagName === 'CANVAS' && el.toDataURL) {
                try { return el.toDataURL('image/png'); } catch(e) { return null; }
              }
              if (el.tagName === 'IMG') {
                const src = el.getAttribute('src') || '';
                if (src.startsWith('data:')) return src;
              }
              return null;
            }
            """,
            selector,
        )
        if isinstance(b64, str) and b64.startswith("data:image"):
            return b64
    except Exception:
        return None
    return None

async def _wait_for_qr(page, *, timeout_ms: int, poll_ms: int, dump_dir: Union[str, Path]) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns:
      (data_url, None)          -> QR found
      (None, None)              -> Already logged in (no QR needed)
      (None, png_diag_path)     -> Timeout, saved diagnostics
    """
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        if await _is_logged_in(page):
            return None, None
        for sel in QR_SELECTORS:
            data_url = await _try_extract_qr_dataurl(page, sel)
            if data_url:
                return data_url, None
        await asyncio.sleep(poll_ms / 1000.0)

    # diagnostics
    diag_dir = Path(dump_dir); diag_dir.mkdir(parents=True, exist_ok=True)
    png_path = str(diag_dir / "whatsapp_qr_not_found.png")
    html_path = str(diag_dir / "whatsapp_qr_not_found.html")
    with contextlib.suppress(Exception): await page.screenshot(path=png_path, full_page=True)
    with contextlib.suppress(Exception):
        html = await page.content()
        await _safe_write_text(html_path, html[:200_000])
    return None, png_path

# --------------- Persistent Context helpers ---------------
async def _open_persistent_context(playwright, session_id: str, headless: bool = True, extra_args=None):
    user_data_dir = _session_dir(session_id)
    chromium_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if extra_args:
        chromium_args += extra_args

    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        headless=headless,
        args=chromium_args,
        viewport={"width": 1280, "height": 900},
        user_agent=DEFAULT_USER_AGENT,
    )
    page = await context.new_page()
    return context, page

async def _ensure_logged_in(session_id: str, *, headless: bool, dump_dir: str, nav_timeout_ms: int, qr_timeout_ms: int) -> dict:
    """
    Open persistent session; if not logged in, wait for QR and publish.
    Returns dict with status ∈ {"already_logged_in","qr_generated","error"}.
    """
    from playwright.async_api import async_playwright  # lazy import
    async with async_playwright() as p:
        ctx, page = await _open_persistent_context(p, session_id, headless=headless)
        try:
            await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle", timeout=nav_timeout_ms)

            if await _is_logged_in(page):
                res = {"status": "already_logged_in", "session": session_id}
                _cache_set(session_id, res)
                return res

            data_url, diag = await _wait_for_qr(page, timeout_ms=qr_timeout_ms, poll_ms=1250, dump_dir=dump_dir)
            if data_url:
                _publish_qr_event(session_id, "qr_generated", b64=data_url)
                res = {"status": "qr_generated", "qr": data_url, "session": session_id}
                _cache_set(session_id, res)
                return res

            if diag is None:
                # Became logged in while waiting
                res = {"status": "already_logged_in", "session": session_id}
                _cache_set(session_id, res)
                return res

            _log_error("QR Generation Error", f"debug:{diag}")
            _publish_qr_event(session_id, "error", diag=diag)
            res = {"status": "error", "message": "QR not found (Playwright)", "diag": diag, "session": session_id}
            _cache_set(session_id, res)
            return res
        finally:
            with contextlib.suppress(Exception): await ctx.close()

# --------------- One-shot non-persistent QR (legacy/simple) ---------------
async def _generate_qr_base64_one_shot(*, headless: bool, dump_dir: Union[str, Path], nav_timeout_ms: int, qr_timeout_ms: int) -> Tuple[Optional[str], Optional[str]]:
    """Non-persistent context; used by get_qr_data_url if needed."""
    from playwright.async_api import async_playwright
    dump_dir = Path(dump_dir); dump_dir.mkdir(parents=True, exist_ok=True)
    console_log_path = dump_dir / "console.log"
    chromium_args = ["--no-sandbox", "--disable-dev-shm-usage"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=chromium_args)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, user_agent=DEFAULT_USER_AGENT, java_script_enabled=True)
        page = await ctx.new_page()
        page.on("console", lambda msg: asyncio.create_task(_append_line(console_log_path, msg.text())))
        try:
            await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle", timeout=nav_timeout_ms)
            data_url, diag = await _wait_for_qr(page, timeout_ms=qr_timeout_ms, poll_ms=1250, dump_dir=dump_dir)
            return data_url, diag
        finally:
            with contextlib.suppress(Exception): await ctx.close()
            with contextlib.suppress(Exception): await browser.close()

# --------------- Whitelisted APIs ---------------
if frappe:
    @frappe.whitelist()
    def get_qr_data_url(device_name: str = "default", headless: int = 1, dump_dir: str = DEFAULT_DUMP_DIR) -> Optional[str]:
        """
        Simple API:
        - If already logged in (persistent), returns None (no QR needed) and pushes no event.
        - If not logged in, extracts QR once and publishes realtime.
        Returns data-url string or None.
        """
        nav_timeout_ms = 120_000
        qr_timeout_ms = 90_000
        # prefer persistent flow so we don't wipe storage
        res = _run_async(_ensure_logged_in(device_name, headless=bool(int(headless)), dump_dir=dump_dir, nav_timeout_ms=nav_timeout_ms, qr_timeout_ms=qr_timeout_ms))
        if res.get("status") == "qr_generated":
            return res.get("qr")
        return None  # already_logged_in or error -> UI can check status via check_qr_status_pw

    @frappe.whitelist()
    def generate_whatsapp_qr_pw(
        session_id: str,
        timeout: int = 60,                 # seconds
        headless: int = 1,
        dump_dir: str = DEFAULT_DUMP_DIR,
    ) -> dict:
        """
        Backward-compatible generator (expected by existing code):
        Returns:
            {"status":"qr_generated","qr":"data:image/png;base64,...","session":...}
            {"status":"already_logged_in","session":...}
            {"status":"error","message":"...","diag":"...","session":...}
        Also publishes realtime 'whatsapp_qr' and sets cache for polling.
        """
        nav_timeout_ms = 120_000
        qr_timeout_ms = 90_000
        if isinstance(timeout, (int, float)) and timeout > 0:
            qr_timeout_ms = int(float(timeout) * 1000)

        try:
            res = _run_async(_ensure_logged_in(session_id, headless=bool(int(headless)), dump_dir=dump_dir, nav_timeout_ms=nav_timeout_ms, qr_timeout_ms=qr_timeout_ms))
            # res already published & cached inside
            # normalize for very old clients that expect "qr_data"
            if res.get("qr") and "qr_data" not in res:
                res["qr_data"] = res["qr"]
            return res
        except Exception as e:
            msg = f"{e}"
            _log_error("PW generate error", msg[:2000])
            _publish_qr_event(session_id, "error", diag=None)
            out = {"status": "error", "message": msg, "diag": None, "session": session_id}
            _cache_set(session_id, out)
            return out

    @frappe.whitelist()
    def check_qr_status_pw(session_id: str) -> dict:
        """
        Status probe for polling UIs.
        Returns:
            {"status":"qr_generated","qr":"...","qr_data":"...","session":...}
            {"status":"already_logged_in","session":...}
            {"status":"error","message":"...","diag":"...","session":...}
            {"status":"waiting","session":...}
        """
        res = _cache_get(session_id)
        if not res:
            return {"status": "waiting", "session": session_id}
        if res.get("qr") and "qr_data" not in res:
            res["qr_data"] = res["qr"]
        return res

    @frappe.whitelist()
    def clear_qr_status_pw(session_id: str) -> None:
        _cache_clear(session_id)

# --------------- Optional CLI smoke test ---------------
def _print(msg: str) -> None:
    sys.stdout.write(msg + "\n"); sys.stdout.flush()

def main_cli(argv: list[str]) -> int:
    # quick one-shot test (non-persistent)
    headless = True; dump_dir = DEFAULT_DUMP_DIR
    i = 0
    while i < len(argv):
        if argv[i] in ("--headful", "--no-headless"): headless = False
        elif argv[i] in ("--dump", "--dump-dir") and i + 1 < len(argv): dump_dir = argv[i + 1]; i += 1
        i += 1

    _print(f"Launching Playwright one-shot (headless={headless}) …")
    try:
        data_url, diag = asyncio.run(_generate_qr_base64_one_shot(headless=headless, dump_dir=dump_dir, nav_timeout_ms=120_000, qr_timeout_ms=90_000))
    except Exception as e:
        _print("ERROR: " + str(e))
        return 2
    if data_url:
        _print("QR extracted successfully (data URL).")
        _print("Preview (first 100): " + data_url[:100] + " …")
        return 0
    else:
        _print("Failed to find QR. Diagnostics: " + str(diag))
        return 1

if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))

__all__ = [
    "get_qr_data_url",
    "generate_whatsapp_qr_pw",
    "check_qr_status_pw",
    "clear_qr_status_pw",
]
