import os
import time
import base64
import io
import urllib.parse
import frappe
from PIL import Image
from playwright.sync_api import sync_playwright

# Reuse session directory util from selenium path
from whatsapp_integration.api.whatsapp_real_qr import (
	_safe_log,
	get_session_directory,
	_get_qr_hash,
)


def _prepare_playwright_env() -> None:
	"""Attempt to ensure Playwright can find a browser binary.

	This helps when the app runs under a different user (e.g. root)
	than the user who executed `playwright install`, by pointing
	PLAYWRIGHT_BROWSERS_PATH to an existing cache if available.
	"""
	try:
		# If already configured, leave as-is
		if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
			return

		candidates = []
		# Linux/macOS common caches
		candidates.append(os.path.expanduser("~/.cache/ms-playwright"))
		candidates.extend([
			"/ms-playwright",
			"/usr/lib/ms-playwright",
			"/usr/local/lib/ms-playwright",
			"/home/frappe/.cache/ms-playwright",
		])

		# Windows common caches
		try:
			userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
			if userprofile:
				candidates.append(os.path.join(userprofile, "AppData", "Local", "ms-playwright"))
		except Exception:
			pass
		try:
			programdata = os.environ.get("PROGRAMDATA")
			if programdata:
				candidates.append(os.path.join(programdata, "ms-playwright"))
		except Exception:
			pass
		# A simple conventional root path that admins may use
		candidates.append("C:/ms-playwright")

		for path in candidates:
			try:
				if path and os.path.isdir(path):
					os.environ["PLAYWRIGHT_BROWSERS_PATH"] = path
					_safe_log(f"PLAYWRIGHT_BROWSERS_PATH set to {path}", "WhatsApp PW Env")
					return
			except Exception:
				pass
	except Exception:
		# Never fail due to env prep
		pass


def _launch_context_with_fallbacks(p, user_data_dir: str, headless: bool = True):
	"""Launch a persistent context trying multiple strategies.

	Order:
	1) Use system Chrome channel if present.
	2) Use default Playwright-managed Chromium.

	If both fail, raise the last exception with guidance.
	"""
	common_args = [
		"--no-sandbox",
		"--disable-dev-shm-usage",
		"--disable-gpu",
		"--lang=en-US,en",
		"--disable-blink-features=AutomationControlled",
		"--window-size=1280,720",
	]
	ua = os.environ.get(
		"WHATSAPP_DESKTOP_UA",
		"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
		"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
	)

	last_err = None

	# Attempt 1: Use system Chrome if installed
	try:
		return p.chromium.launch_persistent_context(
			user_data_dir=user_data_dir,
			headless=headless,
			channel=os.environ.get("PLAYWRIGHT_CHANNEL", "chrome"),
			args=common_args,
			locale="en-US",
			user_agent=ua,
		)
	except Exception as e:
		last_err = e
		_safe_log(f"Playwright channel launch failed: {e}", "WhatsApp PW Launch")

	# Attempt 1b (Windows): try Microsoft Edge channel
	try:
		if os.name == "nt":
			return p.chromium.launch_persistent_context(
				user_data_dir=user_data_dir,
				headless=headless,
				channel="msedge",
				args=common_args,
				locale="en-US",
				user_agent=ua,
			)
	except Exception as e:
		last_err = e
		_safe_log(f"Playwright msedge launch failed: {e}", "WhatsApp PW Launch")

	# Attempt 2: Default bundled Chromium (requires `playwright install` for this user)
	try:
		return p.chromium.launch_persistent_context(
			user_data_dir=user_data_dir,
			headless=headless,
			args=common_args,
			locale="en-US",
			user_agent=ua,
		)
	except Exception as e:
		last_err = e
		_safe_log(f"Playwright default launch failed: {e}", "WhatsApp PW Launch")

	# If we are here, provide actionable error
	msg = (
		"Playwright browser not found. Install browsers for the runtime user or "
		"set PLAYWRIGHT_BROWSERS_PATH to a shared cache. Suggested commands: "
		"`python -m playwright install --with-deps chromium` (as the SAME user running bench) "
		"or install system Chrome and set `PLAYWRIGHT_CHANNEL=chrome`."
	)
	raise RuntimeError(f"{msg} Original error: {last_err}")


def _resolve_user_data_dir(session_id: str) -> str:
	try:
		return get_session_directory(session_id)
	except Exception as e:
		_safe_log(f"Playwright session dir fallback: {e}", "WhatsApp PW Session Dir")
		import tempfile
		return tempfile.mkdtemp(prefix=f"whatsapp_pw_{session_id}_")


def _extract_qr_from_page(page) -> str | None:
	try:
		# Prefer exact canvas with data-ref
		selectors = [
			"canvas[data-ref]",
			"div[data-ref] canvas",
			"[data-testid='qrcode'] canvas",
			"[data-testid='qrcode'] > canvas",
			"canvas[aria-label*='QR']",
			".landing-window canvas",
			".landing-wrapper canvas",
			".landing-window [role='img'] canvas",
		]
		canvas = None
		for sel in selectors:
			canvas = page.query_selector(sel)
			if canvas:
				break
		if not canvas:
			# Fallback: any square-ish canvas
			for c in page.query_selector_all("canvas"):
				box = c.bounding_box() or {}
				w = box.get("width") or 0
				h = box.get("height") or 0
				if w >= 180 and h >= 180 and abs(w - h) < 60:
					canvas = c
					break
		if canvas:
			# Try direct toDataURL via DOM
			data_url = page.evaluate(
				"""(c)=>{ try { return c.toDataURL('image/png'); } catch(e){ return null; } }""",
				canvas,
			)
			if isinstance(data_url, str) and data_url.startswith("data:image"):
				return data_url
			# Fallback: crop screenshot
			box = canvas.bounding_box()
			if box:
				shot = page.screenshot(clip=box)
				img = Image.open(io.BytesIO(shot)).convert("RGB")
				buf = io.BytesIO()
				img.save(buf, format="PNG", quality=100)
				return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
	except Exception as e:
		_safe_log(f"Playwright QR extract failed: {e}", "WhatsApp PW QR")
	return None


def _maybe_dismiss_and_reload_qr(page) -> None:
	"""Best-effort: close interstitials and refresh QR if a control is present."""
	try:
		# Cookie/intro prompts
		for sel in [
			"button:has-text('Use Here')",
			"button:has-text('Continue')",
			"button:has-text('OK')",
			"button:has-text('Accept')",
			"[data-testid='accept']",
		]:
			el = page.query_selector(sel)
			if el:
				el.click()
				time.sleep(0.3)
	except Exception:
		pass
	# Try to reload QR if such a button exists
	for sel in [
		"[aria-label*='reload QR']",
		"[aria-label*='Reload']",
		"[data-testid*='refresh']",
		"button:has-text('Reload')",
		"button:has-text('Refresh')",
		"span:has-text('Click to reload QR code')",
	]:
		try:
			btn = page.query_selector(sel)
			if btn:
				btn.click()
				time.sleep(0.5)
				break
		except Exception:
			pass


@frappe.whitelist()
def generate_whatsapp_qr_pw(session_id: str, timeout: int = 60):
    """Generate QR with Playwright (headless, persistent profile)."""
    user_data_dir = _resolve_user_data_dir(session_id)
    _prepare_playwright_env()
    try:
        with sync_playwright() as p:
			# Allow headful debug via env: WHATSAPP_PW_HEADLESS=0 or 'false'
			h_env = (os.environ.get("WHATSAPP_PW_HEADLESS") or "1").lower()
			headless = not (h_env in {"0", "false", "no"})
			browser = _launch_context_with_fallbacks(p, user_data_dir, headless=headless)
			page = browser.new_page()
			page.set_default_timeout(15000)
			page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
			# quick settle
			time.sleep(2)
			# extra settle for heavy first load
			try:
				page.wait_for_load_state("networkidle", timeout=15000)
			except Exception:
				pass

			# Handle occasional interstitials/cookie prompts quietly
			_maybe_dismiss_and_reload_qr(page)

			# Already connected?
			if page.query_selector("[data-testid='chat-list'], [data-testid='sidebar'], [data-testid='pane-side'], [data-testid='conversation-panel-body']"):
				browser.close()
				return {
					"status": "already_connected",
					"session": session_id,
					"message": "WhatsApp session already connected",
				}

			# Find QR
			start = time.time()
			qr_data = None
			# Try waiting for QR containers or any viable canvas
			wait_targets = [
				"[data-testid='qrcode'] canvas",
				"div[data-ref] canvas",
				"canvas[data-ref]",
				".landing-window canvas",
				"canvas",
			]
			for sel in wait_targets:
				try:
					page.wait_for_selector(sel, state="visible", timeout=5000)
					break
				except Exception:
					pass
			while time.time() - start < timeout:
				_maybe_dismiss_and_reload_qr(page)
				qr_data = _extract_qr_from_page(page)
				if qr_data:
					break
				time.sleep(0.5)

			if not qr_data:
				# Capture diagnostic screenshot
				diag_path = None
				try:
					shot = page.screenshot(full_page=True)
					diag_dir = _resolve_user_data_dir(f"diag_{session_id}")
					os.makedirs(diag_dir, exist_ok=True)
					diag_path = os.path.join(diag_dir, f"whatsapp_qr_not_found_{int(time.time())}.png")
					with open(diag_path, "wb") as f:
						f.write(shot)
					_safe_log(f"QR not found; saved screenshot at {diag_path}", "WhatsApp PW QR")
				except Exception as diag_err:
					_safe_log(f"Failed to save diagnostic screenshot: {diag_err}", "WhatsApp PW QR")
				browser.close()
				raise Exception(f"QR element not found (PW){' | debug:' + diag_path if diag_path else ''}")

			# Keep browser context alive briefly so user can scan
			# We don't block here; client can poll status
			return {
				"status": "qr_ready",
				"qr": qr_data,
				"qr_data": qr_data,
				"session": session_id,
				"message": "WhatsApp QR (Playwright) generated",
				"session_dir": user_data_dir,
			}
	except Exception as e:
		_safe_log(f"PW generate error: {e}", "WhatsApp PW QR")
		return {"status": "error", "message": str(e), "session": session_id}


@frappe.whitelist()
def check_qr_status_pw(session_id: str):
	"""Check connection or refresh QR with Playwright using persisted profile."""
	user_data_dir = _resolve_user_data_dir(session_id)
    _prepare_playwright_env()
    try:
        with sync_playwright() as p:
			h_env = (os.environ.get("WHATSAPP_PW_HEADLESS") or "1").lower()
			headless = not (h_env in {"0", "false", "no"})
			browser = _launch_context_with_fallbacks(p, user_data_dir, headless=headless)
			page = browser.new_page()
			page.set_default_timeout(15000)
			page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
			time.sleep(2)
			try:
				page.wait_for_load_state("networkidle", timeout=15000)
			except Exception:
				pass

			if page.query_selector("[data-testid='chat-list'], [data-testid='sidebar'], [data-testid='pane-side'], [data-testid='conversation-panel-body']"):
				browser.close()
				return {
					"status": "connected",
					"session": session_id,
					"message": "WhatsApp session already connected",
				}

			_maybe_dismiss_and_reload_qr(page)
			qr = _extract_qr_from_page(page)
			browser.close()
			if qr:
				return {
					"status": "qr_ready",
					"qr": qr,
					"qr_data": qr,
					"session": session_id,
					"message": "QR available - scan to connect",
				}
			return {"status": "not_found", "session": session_id, "message": "QR not found in current session"}
	except Exception as e:
		_safe_log(f"PW status error: {e}", "WhatsApp PW QR")
		return {
			"status": "error",
			"message": str(e),
			"session": session_id,
		}


@frappe.whitelist()
def send_message_pw(session_id: str, phone_number: str, message: str):
	"""Send message via Playwright (requires linked device in this profile)."""
	user_data_dir = _resolve_user_data_dir(session_id)
	_prepare_playwright_env()
    try:
		dest = "".join(filter(str.isdigit, phone_number or ""))
		if not dest:
			return {"success": False, "error": "Invalid destination number"}
		text = urllib.parse.quote(message or "")
		chat_url = f"https://web.whatsapp.com/send?phone={dest}&text={text}"

        with sync_playwright() as p:
			h_env = (os.environ.get("WHATSAPP_PW_HEADLESS") or "1").lower()
			headless = not (h_env in {"0", "false", "no"})
			browser = _launch_context_with_fallbacks(p, user_data_dir, headless=headless)
			page = browser.new_page()
			page.set_default_timeout(20000)
			page.goto(chat_url, wait_until="domcontentloaded")
			# wait send button
			page.wait_for_selector("[data-testid='send']")
			page.click("[data-testid='send']")
			browser.close()
			return {
				"success": True,
				"message_id": f"{session_id}_{dest}_{int(time.time())}",
				"timestamp": frappe.utils.now(),
			}
	except Exception as e:
		_safe_log(f"PW send error: {e}", "WhatsApp PW Send")
		return {"success": False, "error": str(e)}


@frappe.whitelist()
def health_check_playwright():
	"""Lightweight readiness probe for Playwright-based service."""
	try:
		with sync_playwright() as p:
			browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
			browser.new_page().close()
			browser.close()
		return {
			"status": "Playwright service ready",
			"headless": True,
			"timestamp": frappe.utils.now(),
		}
	except Exception as e:
		_safe_log(f"Playwright health check failed: {e}", "WhatsApp PW Health")
		return {
			"status": "Playwright health check failed",
			"error": str(e),
			"timestamp": frappe.utils.now(),
		}


