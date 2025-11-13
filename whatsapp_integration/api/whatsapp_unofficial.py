import re
import frappe

def _digits_only(phone: str) -> str:
	return re.sub(r"\D", "", phone or "")

def send_unofficial(device_name, number, message):
	"""Enqueue a 'send_message' command for the active Playwright service."""
	try:
		from whatsapp_integration.api.whatsapp_playwright import (
			_session_command_queues,
			_session_command_results,
			_pw_lock,
		)

		device_doc = frappe.get_doc("WhatsApp Device", device_name)
		session_id = device_doc.number
		if not session_id:
			raise Exception(f"Device '{device_name}' does not have a number set.")

		cmd_id = f"send_{session_id}_{frappe.generate_hash(length=8)}"
		command = {
			"id": cmd_id,
			"type": "send_message",
			"phone_number": number,
			"message": message,
		}

		with _pw_lock:
			_session_command_queues.setdefault(session_id, []).append(command)

		# Wait for the result
		timeout = 30  # seconds
		deadline = frappe.utils.now_datetime() + frappe.utils.datetime.timedelta(seconds=timeout)
		result = None
		while frappe.utils.now_datetime() < deadline:
			with _pw_lock:
				if cmd_id in _session_command_results:
					result = _session_command_results.pop(cmd_id)
					break
			frappe.sleep(0.5)

		if result is None:
			raise Exception("Request timed out. The WhatsApp service may be disconnected or busy.")

		if not result.get("success"):
			raise Exception(result.get("error", "Failed to send message via active session"))

		return result

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
