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
		canvas = page.query_selector("canvas[data-ref]") or page.query_selector("div[data-ref] canvas")
		if not canvas:
			# Fallback: any square-ish canvas
			for c in page.query_selector_all("canvas"):
				box = c.bounding_box() or {}
				w = box.get("width") or 0
				h = box.get("height") or 0
				if w >= 200 and h >= 200 and abs(w - h) < 50:
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


@frappe.whitelist()
def generate_whatsapp_qr_pw(session_id: str, timeout: int = 60):
	"""Generate QR with Playwright (headless, persistent profile)."""
	user_data_dir = _resolve_user_data_dir(session_id)
	try:
		with sync_playwright() as p:
			browser = p.chromium.launch_persistent_context(
				user_data_dir=user_data_dir,
				headless=True,
				args=[
					"--no-sandbox",
					"--disable-dev-shm-usage",
					"--disable-gpu",
					"--lang=en-US,en",
					"--disable-blink-features=AutomationControlled",
					"--window-size=1280,720",
				],
				locale="en-US",
			)
			page = browser.new_page()
			page.set_default_timeout(15000)
			page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
			# quick settle
			time.sleep(2)

			# Already connected?
			if page.query_selector("[data-testid='chat-list'], [data-testid='sidebar'], [data-testid='pane-side'], [data-testid='conversation-panel-body']"):
				browser.close()
				return {"status": "already_connected", "session": session_id}

			# Find QR
			start = time.time()
			qr_data = None
			while time.time() - start < timeout:
				qr_data = _extract_qr_from_page(page)
				if qr_data:
					break
				time.sleep(0.5)

			if not qr_data:
				browser.close()
				raise Exception("QR element not found (PW)")

			# Keep browser context alive briefly so user can scan
			# We don't block here; client can poll status
			return {
				"status": "qr_generated",
				"qr": qr_data,
				"session": session_id,
				"message": "WhatsApp QR (Playwright) generated",
				"session_dir": user_data_dir,
			}
	except Exception as e:
		_safe_log(f"PW generate error: {e}", "WhatsApp PW QR")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def check_qr_status_pw(session_id: str):
	"""Check connection or refresh QR with Playwright using persisted profile."""
	user_data_dir = _resolve_user_data_dir(session_id)
	try:
		with sync_playwright() as p:
			browser = p.chromium.launch_persistent_context(
				user_data_dir=user_data_dir,
				headless=True,
				args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--lang=en-US,en"],
				locale="en-US",
			)
			page = browser.new_page()
			page.set_default_timeout(15000)
			page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
			time.sleep(2)

			if page.query_selector("[data-testid='chat-list'], [data-testid='sidebar'], [data-testid='pane-side'], [data-testid='conversation-panel-body']"):
				browser.close()
				return {"status": "connected", "session": session_id}

			qr = _extract_qr_from_page(page)
			browser.close()
			if qr:
				return {"status": "qr_ready", "qr_data": qr, "session": session_id}
			return {"status": "not_found", "session": session_id}
	except Exception as e:
		_safe_log(f"PW status error: {e}", "WhatsApp PW QR")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def send_message_pw(session_id: str, phone_number: str, message: str):
	"""Send message via Playwright (requires linked device in this profile)."""
	user_data_dir = _resolve_user_data_dir(session_id)
	try:
		dest = "".join(filter(str.isdigit, phone_number or ""))
		if not dest:
			return {"success": False, "error": "Invalid destination number"}
		text = urllib.parse.quote(message or "")
		chat_url = f"https://web.whatsapp.com/send?phone={dest}&text={text}"

		with sync_playwright() as p:
			browser = p.chromium.launch_persistent_context(
				user_data_dir=user_data_dir,
				headless=True,
				args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--lang=en-US,en"],
				locale="en-US",
			)
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


