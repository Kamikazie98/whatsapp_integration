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
import base64
import contextlib
import hashlib
import logging
import os
import shutil
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple, Union

# ---- Dedicated File Logger ---
LOG_FILE = "/tmp/whatsapp_integration_playwright.log"
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
wa_logger = logging.getLogger("whatsapp_playwright")
wa_logger.setLevel(logging.DEBUG)
# Avoid duplicate handlers if the script is reloaded
if not wa_logger.handlers:
    wa_logger.addHandler(file_handler)
# ---- End Logger ----


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
    'div[class*="_akau"] canvas',          # Primary selector from user-provided HTML
    'canvas[aria-label="Scan this QR code to link a device!"]', # New aria-label
    'canvas[aria-label="Scan me!"]',      # Legacy
    'div[data-testid="qrcode"] canvas',   # Legacy
]

LOGIN_MARKERS = [
    'div[data-testid="chat-list-search"]',
    'div[aria-label="Chat list"]',
    'header[data-testid="chatlist-header"]',
    'div[data-testid="chat-list"]',
    'div[data-testid="conversation-panel-wrapper"]',
    'div[data-testid="conversation-panel-messages"]',
    'div[data-testid="conversation-panel"]',
    '[data-testid="conversation-panel-body"]',
    'div[data-testid="chat"]',
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_DUMP_DIR = "/tmp/whatsapp_diag"
QR_MONITOR_SECS = 600  # keep refreshing QR for 10 minutes
# WhatsApp rotates the QR roughly every 20 seconds, so we only need to poll that often
QR_REFRESH_INTERVAL = 20.0
QR_WAIT_POLL_INTERVAL = 0.5

_active_pw_threads: dict[str, threading.Thread] = {}
_active_pw_stop: dict[str, threading.Event] = {}
_active_pw_state: dict[str, dict] = {}
_active_pw_profiles: dict[str, Path] = {}
_session_dump_dirs: dict[str, Path] = {}
_session_user_hints: dict[str, Optional[str]] = {}
_session_storage_files: dict[str, Path] = {}
_pw_lock = threading.Lock()


def _current_site_name() -> Optional[str]:
    if frappe:
        try:
            site = getattr(frappe.local, "site", None)
            if site:
                return site
        except Exception:
            pass
    env_site = os.environ.get("FRAPPE_SITE")
    return env_site


def _current_session_user() -> Optional[str]:
    if not frappe:
        return None
    try:
        return getattr(getattr(frappe, "session", None), "user", None)
    except Exception:
        return None

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
    snap = dict(payload)
    snap.setdefault("session", session_id)
    with _pw_lock:
        _active_pw_state[session_id] = snap
    if not frappe:
        return
    try:
        frappe.cache().set_value(_cache_key(session_id), snap, expires_in_sec=ttl)
    except Exception:
        pass

def _cache_get(session_id: str) -> Optional[dict]:
    with _pw_lock:
        snap = _active_pw_state.get(session_id)
        if snap:
            return dict(snap)
    if not frappe:
        return snap
    try:
        res = frappe.cache().get_value(_cache_key(session_id))
        if res:
            with _pw_lock:
                _active_pw_state[session_id] = dict(res)
        return res
    except Exception:
        return snap

def _cache_clear(session_id: str) -> None:
    with _pw_lock:
        _active_pw_state.pop(session_id, None)
    if not frappe:
        return
    try:
        frappe.cache().delete_value(_cache_key(session_id))
    except Exception:
        pass


def _qr_hash(data_url: Optional[str]) -> Optional[str]:
    """Return a short hash of the QR data so we can detect refreshes."""
    if not data_url or not data_url.startswith("data:image"):
        return None
    try:
        header_split = data_url.split(",", 1)
        body = header_split[1] if len(header_split) == 2 else data_url[22:]
        return hashlib.md5(body.encode("ascii", "ignore")).hexdigest()
    except Exception:
        return None


def _store_status(
    session_id: str,
    status: str,
    *,
    qr: Optional[str] = None,
    message: Optional[str] = None,
    diag: Optional[str] = None,
    publish: bool = False,
    ttl: int = QR_MONITOR_SECS + 60,
) -> dict:
    """Persist the latest status in both in-memory map and frappe cache."""
    prev = _active_pw_state.get(session_id, {})
    payload = {"status": status, "session": session_id}
    if qr:
        payload["qr"] = qr
        payload.setdefault("qr_data", qr)
    elif prev.get("qr"):
        payload["qr"] = prev["qr"]
        if prev.get("qr_data"):
            payload["qr_data"] = prev["qr_data"]
    if message:
        payload["message"] = message
    if diag:
        payload["diag"] = diag
    _active_pw_state[session_id] = payload
    _cache_set(session_id, payload, ttl=ttl)
    _sync_device_doc(session_id, payload)
    if publish:
        _publish_qr_event(session_id, status, b64=qr, diag=diag)
    return payload


def _sync_device_doc(session_id: str, payload: dict) -> None:
    if not frappe:
        return
    try:
        if not frappe.db.exists("WhatsApp Device", session_id):
            return
        updates: dict[str, object] = {}
        status = payload.get("status")
        if status == "connected":
            updates["status"] = "Connected"
            try:
                updates["last_sync"] = frappe.utils.now()
            except Exception:
                pass
        elif status in {"qr_ready", "qr_generated"}:
            updates["status"] = "QR Generated"
            qr_data = payload.get("qr") or payload.get("qr_data")
            if qr_data:
                updates["qr_code"] = qr_data
        elif status == "error":
            updates["status"] = "Disconnected"
        if updates:
            frappe.db.set_value("WhatsApp Device", session_id, updates)
    except Exception:
        pass


async def _persist_storage_state(context, session_id: str, dump_dir: Union[str, Path]) -> Optional[Path]:
    """Persist current storage so other Playwright sessions can reuse the login."""
    target = None
    try:
        target = _storage_state_path(session_id, dump_dir, ensure_parent=True)
        await context.storage_state(path=str(target))
        with _pw_lock:
            _session_storage_files[session_id] = target
        _log_info(f"Session state for '{session_id}' persisted successfully.", f"Path: {target}")
        return target
    except Exception as exc:
        _log_error(
            f"Failed to persist session state for '{session_id}'",
            f"Path: {target if target else 'unknown'}\n{exc}",
        )
        return None


def _profile_dir_path(session_id: str, dump_dir: Union[str, Path]) -> Path:
    base = Path(dump_dir) / "pw_profiles"
    safe = session_id.replace("/", "_").replace("\\", "_")
    return base / safe


def _session_profile_dir(session_id: str, dump_dir: Union[str, Path]) -> Path:
    """Return a stable Chrome profile directory per logical session."""
    profile = None
    try:
        profile = _profile_dir_path(session_id, dump_dir)
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.mkdir(parents=True, exist_ok=True)
        return profile
    except Exception as exc:
        _log_error(f"Failed to create profile dir for session '{session_id}'", f"Path: {profile}\n{exc}")
        raise


def _storage_state_path(
    session_id: str,
    dump_dir: Union[str, Path],
    *,
    ensure_parent: bool = False,
) -> Path:
    base = None
    try:
        base = Path(dump_dir) / "storage_states"
        if ensure_parent:
            base.mkdir(parents=True, exist_ok=True)
        safe = session_id.replace("/", "_").replace("\\", "_")
        return base / f"{safe}.json"
    except Exception as exc:
        _log_error(f"Failed to create storage state path for session '{session_id}'", f"Base: {base}\n{exc}")
        raise


def _digits_only(phone: Optional[str]) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def _wait_for_status(
    session_id: str,
    targets: set[str],
    timeout_s: float,
) -> Optional[dict]:
    """Poll cache until one of the desired statuses (or error/connected) appear."""
    deadline = time.time() + max(timeout_s, 0)
    while time.time() < deadline:
        payload = _cache_get(session_id)
        if payload:
            state = payload.get("status")
            if state in targets:
                return payload
            if state in {"error"}:
                return payload
        time.sleep(QR_WAIT_POLL_INTERVAL)
    return _cache_get(session_id)

# --------------- Playwright core ---------------
async def _is_logged_in(page) -> bool:
    for marker in LOGIN_MARKERS:
        with contextlib.suppress(Exception):
            locator = page.locator(marker)
            if await locator.count() == 0:
                continue
            first = locator.first
            if await first.is_visible():
                return True
            if await first.evaluate(
                """(el) => {
                    if (!el) return false;
                    const style = window.getComputedStyle(el);
                    return style && style.visibility !== "hidden" && style.display !== "none" && el.offsetParent !== null;
                }"""
            ):
                return True
    with contextlib.suppress(Exception):
        detected = await page.evaluate(
            """
            () => {
                try {
                    const mainSelectors = [
                        '[data-testid="chat-list"]',
                        '[data-testid="conversation-panel-body"]',
                        '[data-testid="conversation-panel-messages"]',
                        'header[data-testid="chatlist-header"]'
                    ];
                    if (mainSelectors.some(sel => document.querySelector(sel))) {
                        return true;
                    }
                    const localStateKeys = ["last-wid", "WASecretBundle", "WABrowserId"];
                    return localStateKeys.some(key => {
                        try {
                            return Boolean(window.localStorage?.getItem(key));
                        } catch (e) {
                            return false;
                        }
                    });
                } catch (err) {
                    return false;
                }
            }
            """
        )
        if detected:
            return True
    return False


async def _wait_for_login(page, timeout_s: float = 15.0) -> bool:
    deadline = time.time() + max(timeout_s, 1.0)
    while time.time() < deadline:
        if await _is_logged_in(page):
            return True
        with contextlib.suppress(Exception):
            qr_visible = await page.locator('div[data-testid="qrcode"], canvas[aria-label="Scan me!"]').first.is_visible()
            if qr_visible:
                return False
        await asyncio.sleep(0.5)
    return await _is_logged_in(page)

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
        pass
    try:
        raw = await elt.screenshot(type="png")
        if isinstance(raw, (bytes, bytearray)):
            return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return None
    return None


async def _snapshot_qr_once(page) -> Optional[str]:
    """Try every selector once to read the current QR as a data URL."""
    for sel in QR_SELECTORS:
        with contextlib.suppress(Exception):
            data_url = await _try_extract_qr_dataurl(page, sel)
            if data_url:
                return data_url
    return None

async def wait_for_qr(
    page,
    *,
    timeout_ms: int = 120_000,  # Increased timeout
    poll_ms: int = 1_500,
    dump_dir: Union[str, Path] = DEFAULT_DUMP_DIR,
) -> Tuple[Optional[str], Optional[str]]:
    """Find QR as data URL. On failure, save diagnostics and return (None, png_path)."""
    _log_info(f"Starting to wait for QR code ({timeout_ms / 1000}s timeout)...")
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        with contextlib.suppress(Exception):
            if await _is_logged_in(page):
                _log_info("Detected logged-in state, attempting to log out to get QR screen.")
                await _logout_if_needed(page)

        for i, sel in enumerate(QR_SELECTORS):
            _log_info(f"  -> Trying QR selector #{i+1}: {sel}")
            data_url = await _try_extract_qr_dataurl(page, sel)
            if data_url:
                _log_info("QR code found successfully.")
                return data_url, None

        await asyncio.sleep(poll_ms / 1000.0)

    _log_error("Timed out waiting for QR code.", f"Timeout was {timeout_ms / 1000}s.")
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
    nav_timeout_ms: int = 150_000,  # Increased timeout
    qr_timeout_ms: int = 120_000,
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


async def _send_message_pw_async(
    *,
    session_id: str,
    phone_number: str,
    message: str,
    dump_dir: Union[str, Path],
    headless: bool,
    timeout_s: int,
) -> dict:
    """Send a WhatsApp message using the saved Playwright storage state."""
    from playwright.async_api import async_playwright

    dest = _digits_only(phone_number)
    if not dest:
        return {"success": False, "error": "Invalid destination number"}

    dump_dir = Path(dump_dir)
    with _pw_lock:
        state_path = _session_storage_files.get(session_id)
        profile_dir = _active_pw_profiles.get(session_id)
    if not state_path:
        state_path = _storage_state_path(session_id, dump_dir)
    state_exists = Path(state_path).exists()
    profile_path: Optional[Path] = None
    if profile_dir:
        profile_path = Path(profile_dir)
    else:
        # derive profile path from dump_dir even across process restarts
        derived = _profile_dir_path(session_id, dump_dir)
        if derived.exists():
            profile_path = derived

    if not state_exists and not (profile_path and profile_path.exists()):
        _log_error(
            f"Cannot send message for '{session_id}': session not connected.",
            f"State file: {state_path} (exists={state_exists}); Profile: {profile_path}",
        )
        return {"success": False, "error": "Device is not connected (session profile missing)"}

    _log_info(f"Attempting to send message via session '{session_id}'", f"To: {phone_number}")
    chromium_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    chat_url = (
        f"{WHATSAPP_WEB_URL}send?"
        f"phone={dest}&text={urllib.parse.quote(message or '', safe='')}"
    )
    timestamp = None
    if frappe:
        with contextlib.suppress(Exception):
            timestamp = frappe.utils.now()

    async with async_playwright() as p:
        # Prefer storage_state; if missing, fall back to persistent profile dir
        if state_exists:
            browser = await p.chromium.launch(headless=headless, args=chromium_args)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=DEFAULT_USER_AGENT,
                java_script_enabled=True,
                storage_state=str(state_path),
            )
        elif profile_path and profile_path.exists():
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=headless,
                args=chromium_args,
                viewport={"width": 1280, "height": 900},
                user_agent=DEFAULT_USER_AGENT,
                java_script_enabled=True,
            )
            browser = None  # managed by context
        else:
            return {"success": False, "error": "Device is not connected (session profile missing)"}

        page = await context.new_page()
        try:
            await page.goto(chat_url, wait_until="networkidle", timeout=max(timeout_s, 5) * 1000)
            if not await _wait_for_login(page, timeout_s=max(timeout_s, 10)):
                return {"success": False, "error": "WhatsApp session not authenticated"}
            with contextlib.suppress(Exception):
                await _persist_storage_state(context, session_id, dump_dir)

            send_selectors = [
                "[data-testid='send']",
                "button[aria-label='Send']",
                "[data-testid='compose-btn-send']",
            ]
            for sel in send_selectors:
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=timeout_s * 1000)
                    await btn.click()
                    break
                except Exception:
                    continue
            else:
                return {"success": False, "error": "Send button not found"}

            await page.wait_for_timeout(1500)
            if not timestamp:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            return {
                "success": True,
                "message_id": f"{session_id}_{dest}_{int(time.time())}",
                "timestamp": timestamp,
            }
        finally:
            with contextlib.suppress(Exception):
                await page.close()
            with contextlib.suppress(Exception):
                await context.close()
            if "browser" in locals() and browser:
                with contextlib.suppress(Exception):
                    await browser.close()


def send_message_pw(
    session_id: str,
    phone_number: str,
    message: str,
    *,
    headless: int = 1,
    dump_dir: str = DEFAULT_DUMP_DIR,
    timeout: int = 30,
) -> dict:
    """Public API to send WhatsApp messages using Playwright session cookies."""
    dump_path = _session_dump_dirs.get(session_id) or Path(dump_dir)
    try:
        return _run_async(
            _send_message_pw_async(
                session_id=session_id,
                phone_number=phone_number,
                message=message,
                dump_dir=dump_path,
                headless=bool(int(headless)),
                timeout_s=int(timeout) if isinstance(timeout, (int, float)) else 30,
            )
        )
    except Exception as exc:
        _log_error("Playwright send failed", str(exc))
        return {"success": False, "error": str(exc)}


# --------------- Background monitor for auto-refresh ---------------
def _ensure_pw_monitor(
    session_id: str,
    *,
    headless: bool,
    dump_dir: Union[str, Path],
    qr_timeout_ms: int,
    announce: bool = True,
) -> None:
    """Start a background Playwright session that keeps QR data fresh."""
    site_hint = _current_site_name()
    session_user = _current_session_user()
    thread: Optional[threading.Thread]
    with _pw_lock:
        thread = _active_pw_threads.get(session_id)
        if thread and thread.is_alive():
            _log_info(f"PW monitor for '{session_id}' is already running.")
            return

        _log_info(f"Starting new PW monitor for '{session_id}'...")
        dump_path = Path(dump_dir)
        dump_path.mkdir(parents=True, exist_ok=True)
        _session_dump_dirs[session_id] = dump_path
        profile_dir = _session_profile_dir(session_id, dump_path)
        _active_pw_profiles[session_id] = profile_dir

        stop_event = threading.Event()
        _active_pw_stop[session_id] = stop_event
        thread = threading.Thread(
            target=_pw_monitor_entry,
            args=(session_id, stop_event, headless, str(dump_path), qr_timeout_ms, site_hint, str(profile_dir)),
            daemon=True,
            name=f"wa-pw-monitor-{session_id}",
        )
        _active_pw_threads[session_id] = thread
        if session_user is not None:
            _session_user_hints[session_id] = session_user
    if announce:
        _store_status(session_id, "starting", message="Launching Playwright session")
    thread.start()


def _pw_monitor_entry(
    session_id: str,
    stop_event: threading.Event,
    headless: bool,
    dump_dir: str,
    qr_timeout_ms: int,
    site: Optional[str] = None,
    profile_dir: Optional[str] = None,
) -> None:
    """Thread entrypoint. Manages Frappe context and retry loop for the async monitor."""
    max_retries = 5
    base_wait = 2.0  # seconds

    for i in range(max_retries):
        if stop_event.is_set():
            _log_info(f"Stopping PW monitor for '{session_id}' by request.")
            break

        ctx_initialized = False
        try:
            if frappe and site:
                try:
                    frappe.init(site=site)
                    ctx_initialized = True
                    frappe.connect()
                except Exception as exc:
                    sys.stderr.write(f"[PW Monitor] Failed to init frappe site '{site}': {exc}\n")

            _log_info(f"Starting PW monitor attempt #{i + 1} for '{session_id}'.")
            asyncio.run(
                _pw_monitor_async(
                    session_id=session_id,
                    stop_event=stop_event,
                    headless=headless,
                    dump_dir=dump_dir,
                    qr_timeout_ms=qr_timeout_ms,
                    profile_dir=profile_dir,
                )
            )
            _log_info(f"PW monitor for '{session_id}' completed its run.")
            break  # success, no retry needed

        except Exception as exc:
            _log_error(f"Playwright monitor for '{session_id}' crashed", f"{exc}")
            if i < max_retries - 1 and not stop_event.is_set():
                wait_time = base_wait * (2**i)
                _log_info(f"Retrying PW monitor for '{session_id}' in {wait_time:.1f}s...")
                stop_event.wait(wait_time)
            else:
                _log_error(f"PW monitor for '{session_id}' failed after max retries.", "Giving up.")
                _store_status(session_id, "error", message="Monitor crashed permanently", publish=True)
                break
        finally:
            if ctx_initialized and frappe:
                with contextlib.suppress(Exception):
                    frappe.destroy()

    with _pw_lock:
        _log_info(f"Cleaning up PW monitor thread references for '{session_id}'.")
        _active_pw_threads.pop(session_id, None)
        _active_pw_stop.pop(session_id, None)
        _session_user_hints.pop(session_id, None)


async def _pw_monitor_async(
    *,
    session_id: str,
    stop_event: threading.Event,
    headless: bool,
    dump_dir: Union[str, Path],
    qr_timeout_ms: int,
    profile_dir: Optional[Union[str, Path]] = None,
) -> None:
    """Long-lived Playwright session that refreshes QR when WhatsApp rotates it."""
    from playwright.async_api import async_playwright

    dump_dir = Path(dump_dir); dump_dir.mkdir(parents=True, exist_ok=True)
    console_log_path = dump_dir / f"{session_id}_console.log"
    chromium_args = ["--no-sandbox", "--disable-dev-shm-usage"]
    profile_path = Path(profile_dir) if profile_dir else _session_profile_dir(session_id, dump_dir)
    profile_path.mkdir(parents=True, exist_ok=True)
    with _pw_lock:
        _active_pw_profiles[session_id] = profile_path
    context = page = None
    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=headless,
                args=chromium_args,
                viewport={"width": 1280, "height": 900},
                user_agent=DEFAULT_USER_AGENT,
                java_script_enabled=True,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("console", lambda msg: asyncio.create_task(_append_line(console_log_path, msg.text())))

            await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle", timeout=120_000)
            data_url, diag = await wait_for_qr(page, timeout_ms=qr_timeout_ms, dump_dir=dump_dir)
            if stop_event.is_set():
                return
            if not data_url:
                _store_status(
                    session_id,
                    "error",
                    message="QR not found (Playwright)",
                    diag=diag,
                    publish=True,
                )
                return

            last_hash = _qr_hash(data_url)
            _store_status(session_id, "qr_generated", qr=data_url, message="QR code ready", publish=True)

            monitor_deadline = time.time() + QR_MONITOR_SECS
            misses = 0
            connected = False

            while not stop_event.is_set():
                with contextlib.suppress(Exception):
                    if await _is_logged_in(page):
                        if not connected:
                            connected = True
                            await _persist_storage_state(context, session_id, dump_dir)
                            _store_status(
                                session_id,
                                "connected",
                                message="Successfully connected to WhatsApp",
                                publish=True,
                            )
                        await asyncio.sleep(5)
                        continue
                    if connected:
                        # Lost connection -> force QR screen again
                        connected = False
                        monitor_deadline = time.time() + QR_MONITOR_SECS
                        await _logout_if_needed(page)

                if not connected and time.time() > monitor_deadline:
                    break

                if connected:
                    await asyncio.sleep(5)
                    continue

                fresh_qr = await _snapshot_qr_once(page)
                if fresh_qr:
                    misses = 0
                    fresh_hash = _qr_hash(fresh_qr)
                    if fresh_hash and fresh_hash != last_hash:
                        last_hash = fresh_hash
                        monitor_deadline = time.time() + QR_MONITOR_SECS
                        _store_status(
                            session_id,
                            "qr_generated",
                            qr=fresh_qr,
                            message="QR refreshed",
                            publish=True,
                        )
                else:
                    misses += 1
                    if misses >= 5:
                        with contextlib.suppress(Exception):
                            await _logout_if_needed(page)
                        misses = 0

                await asyncio.sleep(QR_REFRESH_INTERVAL)
    except Exception as exc:
        _store_status(session_id, "error", message=str(exc), publish=True)
    finally:
        with contextlib.suppress(Exception):
            if page:
                await page.close()
        with contextlib.suppress(Exception):
            if context:
                await context.close()
        with _pw_lock:
            _active_pw_profiles.pop(session_id, None)

# --------------- Frappe integration ---------------
def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _log_info(title: str, detail: str | None = None) -> None:
    msg = f"{title}{f' - {detail}' if detail else ''}"
    wa_logger.info(msg)
    if not frappe:
        return
    try:
        frappe.logger("whatsapp").info(title, detail)
    except Exception:
        pass


def _log_error(title: str, detail: str) -> None:
    msg = f"{title}{f' - {detail}' if detail else ''}"
    wa_logger.error(msg)
    if not frappe:
        return
    try:
        frappe.log_error(title, detail)
    except Exception:
        pass

def _publish_qr_event(session_id: str, status: str, *, b64: Optional[str] = None, diag: Optional[str] = None) -> None:
    if not frappe:
        return
    try:
        user = None
        try:
            user = getattr(getattr(frappe, "session", None), "user", None)
        except Exception:
            user = None
        if not user:
            with _pw_lock:
                user = _session_user_hints.get(session_id)
        frappe.publish_realtime(
            event="whatsapp_qr",
            message={"device": session_id, "status": status, "qr": b64, "diag": diag},
            user=user,
        )
    except Exception as e:
        _log_error("Realtime publish failed", str(e))

# Whitelisted simple API (returns string)
if frappe:
    @frappe.whitelist()
    def get_qr_data_url(device_name: str = "default", headless: int = 1, dump_dir: str = DEFAULT_DUMP_DIR) -> Optional[str]:
        """Generate QR and publish. Returns data-url string or None."""
        qr_timeout_ms = 90_000
        _ensure_pw_monitor(
            device_name,
            headless=bool(int(headless)),
            dump_dir=dump_dir,
            qr_timeout_ms=qr_timeout_ms,
        )
        result = _wait_for_status(
            device_name,
            targets={"qr_generated", "connected", "error"},
            timeout_s=qr_timeout_ms / 1000.0,
        )
        if not result:
            return None
        status = result.get("status")
        if status == "qr_generated":
            return result.get("qr") or result.get("qr_data")
        if status == "connected":
            return None
        if status == "error":
            return None
        return None

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

        _ensure_pw_monitor(
            session_id,
            headless=bool(int(headless)),
            dump_dir=dump_dir,
            qr_timeout_ms=qr_timeout_ms,
            announce=False,
        )
        wait_payload = _wait_for_status(
            session_id,
            targets={"qr_generated", "connected", "error"},
            timeout_s=qr_timeout_ms / 1000.0,
        )
        if wait_payload:
            return wait_payload
        return {"status": "waiting", "session": session_id, "message": "QR generation timed out"}

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
            return {"status": "waiting", "session": session_id}
        payload = dict(res)
        if payload.get("qr") and "qr_data" not in payload:
            payload["qr_data"] = payload["qr"]
        return payload

    @frappe.whitelist()
    def clear_qr_status_pw(session_id: str) -> None:
        """Optional: clear cache after success or when starting over."""
        thread = None
        event = None
        profile_dir: Optional[Path] = None
        dump_dir: Optional[Path] = None
        storage_file: Optional[Path] = None
        with _pw_lock:
            event = _active_pw_stop.pop(session_id, None)
            thread = _active_pw_threads.pop(session_id, None)
            _session_user_hints.pop(session_id, None)
            profile_dir = _active_pw_profiles.pop(session_id, None)
            dump_dir = _session_dump_dirs.pop(session_id, None)
            storage_file = _session_storage_files.pop(session_id, None)
        if event:
            event.set()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        _active_pw_state.pop(session_id, None)
        _cache_clear(session_id)
        cleanup_path = profile_dir
        if not cleanup_path and dump_dir:
            cleanup_path = _profile_dir_path(session_id, dump_dir)
        if cleanup_path:
            shutil.rmtree(cleanup_path, ignore_errors=True)
        if not storage_file and dump_dir:
            storage_file = _storage_state_path(session_id, dump_dir)
        if storage_file:
            with contextlib.suppress(Exception):
                storage_file.unlink(missing_ok=True)

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
    "send_message_pw",
]
