# -*- coding: utf-8 -*-
"""
WhatsApp Web QR via Playwright (Unofficial) — Final, backward-compatible.

Features:
- Robust QR extraction (multiple selectors, storage reset)
- Headless-friendly (container flags included)
- Realtime publish to Desk (event='whatsapp_qr')
- Backward-compatible wrappers:
    - generate_whatsapp_qr_pw(session_id, timeout, headless, dump_dir) -> dict
    - check_qr_status_pw(session_id) -> dict
- Simple API:
    - get_qr_data_url(device_name, headless, dump_dir) -> str|None
- Status cache in frappe.cache() for polling
"""

from __future__ import annotations
import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Union

# ---- Failsafe: ensure Playwright browsers path & HOME are correct for bench ----
os.environ.setdefault("HOME", "/home/frappe")
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/home/frappe/.cache/ms-playwright")

try:
    import frappe  # type: ignore
except Exception:
    frappe = None  # allows local CLI runs

# ---------------- Tunables ----------------
WHATSAPP_WEB_URL = "https://web.whatsapp.com/"

QR_SELECTORS = [
    'div[data-testid="qrcode"] canvas',
    'canvas[aria-label="Scan me!"]',
    'div[data-ref] canvas',               # older builds
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

# --------------- Small helpers ---------------
async def _safe_write_text(path: Union[str, Path], text: str) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

async def _append_line(path: Union[str, Path], line: str) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")

# --------------- Cache helpers (status) ---------------
def _cache_key(session_id: str) -> str:
    return f"wa_qr_status::{session_id}"

def _cache_set(session_id: str, payload: dict, ttl: int = 300) -> None:
    """Store status for polling (default TTL 5 minutes)."""
    if not frappe:
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

# --------------- Playwright core ---------------
async def _is_logged_in(page) -> bool:
    for marker in LOGIN_MARKERS:
        with contextlib.suppress(Exception):
            if await page.locator(marker).first.is_visible():
                return True
    return False

async def _logout_if_needed(page) -> None:
    """Clear storages to force QR screen."""
    await page.context.clear_cookies()
    await page.evaluate(
        """
        () => {
          try { localStorage.clear(); } catch(e) {}
          try { sessionStorage.clear(); } catch(e) {}
          try {
            if (window.indexedDB && indexedDB.databases) {
              return indexedDB.databases().then(dbs => {
                dbs.forEach(db => { try { indexedDB.deleteDatabase(db.name); } catch(e) {} });
              });
            }
          } catch(e) {}
          return null;
        }
        """
    )
    await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle")

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

async def wait_for_qr(
    page,
    *,
    timeout_ms: int = 90_000,
    poll_ms: int = 1_250,
    dump_dir: Union[str, Path] = DEFAULT_DUMP_DIR,
) -> Tuple[Optional[str], Optional[str]]:
    """Find QR as data URL. On failure, save diagnostics and return (None, png_path)."""
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        with contextlib.suppress(Exception):
            if await _is_logged_in(page):
                await _logout_if_needed(page)

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

async def generate_qr_base64(
    *,
    headless: bool = True,
    user_agent: Optional[str] = None,
    dump_dir: Union[str, Path] = DEFAULT_DUMP_DIR,
    nav_timeout_ms: int = 120_000,
    qr_timeout_ms: int = 90_000,
    proxy: Optional[dict] = None,
    extra_browser_args: Optional[list] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (data:image/png;base64,..., None) on success; (None, diag_png_path) on failure."""
    from playwright.async_api import async_playwright  # lazy import

    dump_dir = Path(dump_dir); dump_dir.mkdir(parents=True, exist_ok=True)
    console_log_path = dump_dir / "console.log"

    chromium_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if extra_browser_args:
        chromium_args.extend(extra_browser_args)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=chromium_args, proxy=proxy)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=user_agent or DEFAULT_USER_AGENT,
            java_script_enabled=True,
        )
        page = await context.new_page()

        page.on("console", lambda msg: asyncio.create_task(_append_line(console_log_path, msg.text())))

        try:
            await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle", timeout=nav_timeout_ms)
            data_url, diag = await wait_for_qr(page, timeout_ms=qr_timeout_ms, dump_dir=dump_dir)
            return data_url, diag
        finally:
            with contextlib.suppress(Exception): await context.close()
            with contextlib.suppress(Exception): await browser.close()

# --------------- Frappe integration ---------------
def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        return asyncio.run(coro)
    return asyncio.run(coro)

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

# Whitelisted simple API (returns string)
if frappe:
    @frappe.whitelist()
    def get_qr_data_url(device_name: str = "default", headless: int = 1, dump_dir: str = DEFAULT_DUMP_DIR) -> Optional[str]:
        """Generate QR and publish. Returns data-url string or None."""
        try:
            data_url, diag = _run_async(generate_qr_base64(headless=bool(int(headless)), dump_dir=dump_dir))
        except Exception as e:
            _log_error("PW generate error", f"{e}")
            _publish_qr_event(device_name, "error", diag=None)
            _cache_set(device_name, {"status": "error", "diag": None, "session": device_name})
            return None

        if not data_url:
            _log_error("QR Generation Error", f"debug:{diag}")
            _publish_qr_event(device_name, "error", diag=diag)
            _cache_set(device_name, {"status": "error", "diag": diag, "session": device_name})
            return None

        _publish_qr_event(device_name, "qr_generated", b64=data_url)
        _cache_set(device_name, {"status": "qr_generated", "qr": data_url, "session": device_name})
        return data_url

# --------------- Backward-compatible APIs ---------------
if frappe:
    @frappe.whitelist()
    def generate_whatsapp_qr_pw(
        session_id: str,
        timeout: int = 60,
        headless: int = 1,
        dump_dir: str = DEFAULT_DUMP_DIR,
    ) -> dict:
        """
        Legacy-compatible generator.
        Returns:
            {"status":"qr_generated","qr":"data:image/png;base64,...","session":session_id}
            or {"status":"error","message":"...","diag":"...","session":session_id}
        Also publishes realtime 'whatsapp_qr' event and sets cache for polling.
        """
        qr_timeout_ms = 90_000
        if isinstance(timeout, (int, float)) and timeout > 0:
            qr_timeout_ms = int(float(timeout) * 1000)

        try:
            data_url, diag = _run_async(
                generate_qr_base64(headless=bool(int(headless)), dump_dir=dump_dir, qr_timeout_ms=qr_timeout_ms)
            )
        except Exception as e:
            msg = f"{e}"
            _log_error("PW generate error", msg[:2000])
            _publish_qr_event(session_id, "error", diag=None)
            res = {"status": "error", "message": msg, "diag": None, "session": session_id}
            _cache_set(session_id, res)
            return res

        if not data_url:
            _log_error("QR Generation Error", f"debug:{diag}")
            _publish_qr_event(session_id, "error", diag=diag)
            res = {"status": "error", "message": "QR not found (Playwright)", "diag": diag, "session": session_id}
            _cache_set(session_id, res)
            return res

        _publish_qr_event(session_id, "qr_generated", b64=data_url)
        res = {"status": "qr_generated", "qr": data_url, "session": session_id, "message": "QR code ready"}
        _cache_set(session_id, res)
        return res

    @frappe.whitelist()
    def check_qr_status_pw(session_id: str) -> dict:
        """
        Legacy-compatible status probe for polling UIs.
        Reads status from cache set by generate_whatsapp_qr_pw / get_qr_data_url.
        Returns one of:
            {"status":"qr_generated","qr":"...","session":...}
            {"status":"error","message":"...","diag":"...","session":...}
            {"status":"waiting","session":...}
        """
        res = _cache_get(session_id)
        if not res:
            # nothing yet in cache → still waiting
            return {"status": "waiting", "session": session_id}
        # normalize keys for older clients that use "qr_data"
        if res.get("qr") and "qr_data" not in res:
            res["qr_data"] = res["qr"]
        return res

    @frappe.whitelist()
    def clear_qr_status_pw(session_id: str) -> None:
        """Optional: clear cache after success or when starting over."""
        _cache_clear(session_id)

# --------------- Optional CLI smoke test ---------------
def _print(msg: str) -> None:
    sys.stdout.write(msg + "\n"); sys.stdout.flush()

def main_cli(argv: list[str]) -> int:
    headless = True; dump_dir = DEFAULT_DUMP_DIR
    i = 0
    while i < len(argv):
        if argv[i] in ("--headful", "--no-headless"): headless = False
        elif argv[i] in ("--dump", "--dump-dir") and i + 1 < len(argv): dump_dir = argv[i + 1]; i += 1
        i += 1

    _print(f"Launching Playwright (headless={headless}) …")
    try:
        data_url, diag = asyncio.run(generate_qr_base64(headless=headless, dump_dir=dump_dir))
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
    "generate_qr_base64",
    "wait_for_qr",
    "get_qr_data_url",
    "generate_whatsapp_qr_pw",
    "check_qr_status_pw",
    "clear_qr_status_pw",
]
