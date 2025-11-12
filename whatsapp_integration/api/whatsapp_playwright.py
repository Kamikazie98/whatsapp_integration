# whatsapp_integration/whatsapp_playwright.py
# -*- coding: utf-8 -*-
"""
Robust WhatsApp Web QR extractor via Playwright.
- Clears session/storage to force QR screen
- Tries multiple selectors for QR (canvas/img)
- Detects "already logged in" markers and resets
- Returns data URL (data:image/png;base64,...) or saves diagnostics
- Works headless by default; can run headful for debugging
- Safe to import without Frappe; integrates if Frappe exists

Usage (Python):
    from whatsapp_integration.whatsapp_playwright import generate_qr_base64
    data_url, diag = asyncio.run(generate_qr_base64(headless=True))

Usage (Frappe):
    frappe.call("whatsapp_integration.whatsapp_playwright.get_qr_data_url", {})
"""

from __future__ import annotations
import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Union

try:
    # Optional: allow this module to be used outside Frappe
    import frappe  # type: ignore
except Exception:
    frappe = None  # noqa

# ---- Tunables ---------------------------------------------------------------

QR_SELECTORS = [
    'div[data-testid="qrcode"] canvas',
    'canvas[aria-label="Scan me!"]',
    'div[data-ref] canvas',               # older builds
    'img[alt="Scan me!"]',                # sometimes img with data: src
    'div[data-testid="qrcode"] img',      # fallback
]

LOGIN_MARKERS = [
    'div[data-testid="chat-list-search"]',  # logged-in shell
    'div[aria-label="Chat list"]',
    'header[data-testid="chatlist-header"]',
]

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ----------------------------------------------------------------------------


async def _safe_write_text(path: Union[str, Path], text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


async def _append_line(path: Union[str, Path], line: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


async def _logout_if_needed(page) -> None:
    """
    Attempt to force QR screen by clearing storages.
    """
    # Clear storages inside the page context
    await page.context.clear_cookies()
    # Clear all storages (local/session/indexedDB)
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
    # Navigate fresh
    await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle")


async def _is_logged_in(page) -> bool:
    for marker in LOGIN_MARKERS:
        try:
            if await page.locator(marker).first.is_visible():
                return True
        except Exception:
            pass
    return False


async def _try_extract_qr_dataurl(page, selector: str) -> Optional[str]:
    """
    If selector points to a visible QR (canvas or data: img), return data URL string.
    """
    elt = page.locator(selector).first
    if not await elt.is_visible():
        return None

    # Evaluate in-page to extract from canvas or data:img
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
    dump_dir: Union[str, Path] = "/tmp/whatsapp_diag",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Try to detect and extract QR as data URL within timeout.
    If fails, take diagnostics (screenshot + html) and return (None, diag_path).
    """
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        # If we are logged in already, clear session to force QR
        with contextlib.suppress(Exception):
            if await _is_logged_in(page):
                await _logout_if_needed(page)

        # Try multiple selectors
        for sel in QR_SELECTORS:
            data_url = await _try_extract_qr_dataurl(page, sel)
            if data_url:
                return data_url, None

        # Sometimes QR is behind a loader; small wait
        await asyncio.sleep(poll_ms / 1000.0)

    # Diagnostics on failure
    diag_dir = Path(dump_dir)
    diag_dir.mkdir(parents=True, exist_ok=True)
    png_path = str(diag_dir / "whatsapp_qr_not_found.png")
    html_path = str(diag_dir / "whatsapp_qr_not_found.html")

    with contextlib.suppress(Exception):
        await page.screenshot(path=png_path, full_page=True)
    with contextlib.suppress(Exception):
        html = await page.content()
        # Avoid giant files; keep useful chunk
        await _safe_write_text(html_path, html[:200_000])

    return None, png_path


async def generate_qr_base64(
    *,
    headless: bool = True,
    user_agent: Optional[str] = None,
    dump_dir: Union[str, Path] = "/tmp/whatsapp_diag",
    nav_timeout_ms: int = 120_000,
    qr_timeout_ms: int = 90_000,
    proxy: Optional[dict] = None,
    extra_browser_args: Optional[list] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Launch Chromium, navigate to WhatsApp Web, and extract QR as a data URL.

    Returns:
        (data_url, diag_path)
        - data_url: str like "data:image/png;base64,..." on success
        - diag_path: path to PNG screenshot with "qr_not_found" on failure (None on success)

    Params:
        headless: run headless browser
        user_agent: override UA string
        dump_dir: directory for diagnostics (console logs, html, screenshots)
        nav_timeout_ms: navigation timeout to WhatsApp Web
        qr_timeout_ms: time budget to find QR
        proxy: Playwright proxy dict, e.g. {"server": "http://host:port", "username": "...", "password": "..."}
        extra_browser_args: list of extra Chromium args
    """
    from playwright.async_api import async_playwright  # local import to avoid hard dep on import time

    dump_dir = Path(dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    console_log_path = dump_dir / "console.log"

    # Prepare launch args (helpful for containers)
    chromium_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    if extra_browser_args:
        chromium_args.extend(extra_browser_args)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=chromium_args,
            proxy=proxy,
        )

        ctx_kwargs = dict(
            viewport={"width": 1280, "height": 900},
            user_agent=user_agent or DEFAULT_USER_AGENT,
            java_script_enabled=True,
            # storage_state not provided: we want clean session
        )
        if proxy:
            # When using proxy, ensure consistent UA
            ctx_kwargs["user_agent"] = user_agent or DEFAULT_USER_AGENT

        context = await browser.new_context(**ctx_kwargs)
        page = await context.new_page()

        # Capture console for diagnostics
        page.on(
            "console",
            lambda msg: asyncio.create_task(_append_line(console_log_path, msg.text())),
        )

        try:
            await page.goto(WHATSAPP_WEB_URL, wait_until="networkidle", timeout=nav_timeout_ms)
            data_url, diag = await wait_for_qr(
                page,
                timeout_ms=qr_timeout_ms,
                dump_dir=dump_dir,
            )
            return data_url, diag
        finally:
            with contextlib.suppress(Exception):
                await context.close()
            with contextlib.suppress(Exception):
                await browser.close()


# -------------------- Frappe Integration Helpers -----------------------------

def _log_error(title: str, detail: str) -> None:
    if frappe is None:
        # fallback to stderr
        sys.stderr.write(f"[ERROR] {title}\n{detail}\n")
        return
    # Frappe truncates title to 140 chars; keep it short and useful
    safe_title = (title or "Error")[:140]
    try:
        frappe.log_error(safe_title, detail)
    except Exception:
        # As a last resort
        sys.stderr.write(f"[FRAPPE LOG ERROR FAILED] {safe_title}\n{detail}\n")


def _publish_qr_event(
    device_name: str,
    status: str,
    *,
    b64: Optional[str] = None,
    diag: Optional[str] = None,
    user: Optional[str] = None,
    event_name: str = "whatsapp_qr",
) -> None:
    """
    Publish realtime event to Desk.
    """
    if frappe is None:
        return
    try:
        frappe.publish_realtime(
            event=event_name,
            message={"device": device_name, "status": status, "b64": b64, "diag": diag},
            user=user or frappe.session.user,
        )
    except Exception as e:
        _log_error("Realtime publish failed", str(e))


def _ensure_playwright_installed_hint() -> str:
    return (
        "Make sure Playwright and browsers are installed:\n"
        "  pip install playwright\n"
        "  playwright install chromium\n"
        "Also ensure OS deps for headless Chromium are present.\n"
    )


# Whitelisted method for Frappe (sync wrapper around async)
def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # If running within an existing event loop (rare in Frappe), create a new one in a thread
        return asyncio.run(coro)  # safest fallback in our context
    return asyncio.run(coro)


if frappe:
    try:
        from frappe.model.document import Document  # noqa
    except Exception:
        pass

    @frappe.whitelist()
    def get_qr_data_url(
        device_name: str = "default",
        headless: int = 1,
        dump_dir: str = "/tmp/whatsapp_diag",
    ) -> Optional[str]:
        """
        Frappe callable: generates QR and publishes it via realtime.
        Returns data URL on success; None on failure (check diag/logs).
        """
        try:
            data_url, diag = _run_async(
                generate_qr_base64(
                    headless=bool(int(headless)),
                    dump_dir=dump_dir,
                )
            )
        except Exception as e:
            _log_error(
                "PW generate error",
                f"{e}\n\n{_ensure_playwright_installed_hint()}",
            )
            _publish_qr_event(device_name, "error", diag=None)
            return None

        if not data_url:
            _log_error(
                "QR Generation Error: QR element not found (PW)",
                f"debug:{diag}",
            )
            _publish_qr_event(device_name, "error", diag=diag)
            return None

        # Publish to UI (preferred: send base64 directly)
        _publish_qr_event(device_name, "ok", b64=data_url)
        return data_url


# ------------------------------- CLI -----------------------------------------

def _print(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def main_cli(argv: list[str]) -> int:
    """
    Minimal CLI for smoke testing:
        python -m whatsapp_integration.whatsapp_playwright [--headful] [--dump /tmp/diag]
    """
    headless = True
    dump_dir = "/tmp/whatsapp_diag"
    i = 0
    while i < len(argv):
        if argv[i] in ("--headful", "--no-headless"):
            headless = False
        elif argv[i] in ("--dump", "--dump-dir") and i + 1 < len(argv):
            dump_dir = argv[i + 1]
            i += 1
        i += 1

    _print(f"Launching Playwright (headless={headless}) …")
    try:
        data_url, diag = asyncio.run(generate_qr_base64(headless=headless, dump_dir=dump_dir))
    except Exception as e:
        _print("ERROR: " + str(e))
        _print(_ensure_playwright_installed_hint())
        return 2

    if data_url:
        _print("QR extracted successfully (data URL).")
        # For terminal brevity, don't print the whole data URL
        _print("Preview (first 100 chars): " + data_url[:100] + " …")
        return 0
    else:
        _print("Failed to find QR. Diagnostics saved at: " + str(diag))
        return 1


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))




def generate_whatsapp_qr_pw(
    device_name: str = "default",
    headless: int = 1,
    dump_dir: str = "/tmp/whatsapp_diag",
    timeout: int | None = None,  # legacy param for backward compatibility
):
    """
    Legacy wrapper for backward compatibility.
    Accepts 'timeout' (ignored) so that older calls don't break.
    Publishes realtime via get_qr_data_url.
    """
    if frappe:
        # Reuse official path; ignore timeout param
        return get_qr_data_url(device_name=device_name, headless=headless, dump_dir=dump_dir)

    # Fallback for non-Frappe environments
    import asyncio
    data_url, _diag = asyncio.run(
        generate_qr_base64(headless=bool(int(headless)), dump_dir=dump_dir)
    )
    return data_url

# Public exports
__all__ = [
    "generate_qr_base64",
    "wait_for_qr",
    "get_qr_data_url",
    "generate_whatsapp_qr_pw",
]