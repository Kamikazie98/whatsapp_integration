import re
import frappe

def _digits_only(phone: str) -> str:
	return re.sub(r"\D", "", phone or "")

def _pick_connected_session():
	"""Pick a usable session id (device number) from WhatsApp Device or Playwright status."""
	# Prefer devices already marked as Connected in the DB
	connected_device = frappe.db.get_value("WhatsApp Device", {"status": "Connected"}, ["name", "number"])
	if connected_device:
		return connected_device[1] or _digits_only(connected_device[0])

	# Fallback: check live status via Playwright
	try:
		from whatsapp_integration.api.whatsapp_playwright import check_qr_status_pw
		devices = frappe.get_all("WhatsApp Device", fields=["name", "number", "status"], order_by="modified desc", limit=5)
		for d in devices:
			# Use number as session_id for PW checks
			session_id_for_check = d.number or _digits_only(d.name)
			if not session_id_for_check:
				continue

			st = check_qr_status_pw(session_id_for_check) or {}
			if st.get("status") == "connected":
				if d.status != "Connected":
					with contextlib.suppress(Exception):
						frappe.db.set_value(
							"WhatsApp Device", d.name, {"status": "Connected", "last_sync": frappe.utils.now()}
						)
				return session_id_for_check # Return the number
	except Exception as e:
		frappe.log_error("WhatsApp Session Picker", f"Error checking live PW status: {e}")

	# Last resort
	any_dev = frappe.db.get_value("WhatsApp Device", {}, "number")
	return any_dev


def send_unofficial(number, message):
	"""Send message using Playwright (Unofficial WhatsApp Web)."""
	try:
		# Session ID is always the device's phone number
		session_id = _pick_connected_session()
		if not session_id:
			raise Exception("No connected WhatsApp device. Please scan QR and connect a device first.")

		dest = _digits_only(number)
		if not dest:
			raise Exception("Invalid destination number")

		# 1) Playwright first
		try:
			from whatsapp_integration.api.whatsapp_playwright import send_message_pw
			result = send_message_pw(session_id, dest, message)
			if isinstance(result, dict) and result.get("success"):
				return result
		except Exception as pw_err:
			frappe.log_error("WhatsApp Unofficial Send", f"PW send failed: {pw_err}")

		# 2) Simple fallback
		from whatsapp_integration.api.whatsapp_python import send_message
		result = send_message(session_id, dest, message)
		if result.get("success"):
			return result
		raise Exception(result.get("error", "Failed to send message"))

	except Exception as e:
		message = f"Failed to send message: {str(e)}"
		frappe.log_error("WhatsApp Unofficial Send", message)
		frappe.throw(message)

@frappe.whitelist()
def check_device_status(session_id):
	"""Check if WhatsApp device is connected using Playwright first."""
	try:
		from whatsapp_integration.api.whatsapp_playwright import check_qr_status_pw
		qr_status = check_qr_status_pw(session_id)
		status = qr_status.get("status")

		return {"status": status or "Unknown"}
		
	except Exception as e:
		frappe.log_error("WhatsApp Status Check", f"Status check error: {str(e)}")
		return {"status": "Error", "message": str(e)}
