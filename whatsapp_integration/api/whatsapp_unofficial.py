import re
import frappe

def _digits_only(phone: str) -> str:
	return re.sub(r"\D", "", phone or "")

def send_unofficial(device_name, number, message):
	"""Send message using Playwright (Unofficial WhatsApp Web)."""
	try:
		device_doc = frappe.get_doc("WhatsApp Device", device_name)
		session_id = device_doc.number # Session ID is always the phone number

		if not session_id:
			raise Exception(f"Device '{device_name}' does not have a number set.")

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
