import frappe
from frappe.model.document import Document


class WhatsAppDevice(Document):
	def _update_fields(self, values: dict | None = None) -> None:
		"""Persist values to DB without full save and mirror them on the instance."""
		if not values:
			return
		frappe.db.set_value(self.doctype, self.name, values)
		for key, val in values.items():
			setattr(self, key, val)

	@frappe.whitelist()
	def mark_connected(self):
		"""Manually mark device as connected (for testing)."""
		try:
			from whatsapp_integration.api.whatsapp_simple import set_session_connected
			result = set_session_connected(self.number)
			if result.get("success"):
				self._update_fields({"status": "Connected", "last_sync": frappe.utils.now()})
				return {"success": True, "message": "Device marked as connected"}
			return {"success": False, "message": "Failed to mark device as connected"}
		except Exception as exc:
			frappe.log_error(f"Mark Connected Error: {exc}", "WhatsApp Mark Connected")
			return {"success": False, "message": f"Error: {exc}"}

	@frappe.whitelist()
	def mark_disconnected(self):
		"""Manually mark device as disconnected (for testing / recovery)."""
		try:
			self._update_fields({"status": "Disconnected"})
			return {"success": True, "message": "Device marked as disconnected"}
		except Exception as exc:
			frappe.log_error(f"Mark Disconnected Error: {exc}", "WhatsApp Mark Disconnected")
			return {"success": False, "message": f"Error: {exc}"}

	@frappe.whitelist()
	def check_connection_status(self):
		"""Check WhatsApp connection status and sync fields."""
		try:
			try:
				from whatsapp_integration.api.whatsapp_playwright import check_qr_status_pw

				status_payload = check_qr_status_pw(self.number)
			except Exception:
				status_payload = None

			if isinstance(status_payload, dict):
				state = status_payload.get("status")
				if state == "connected":
					if self.status != "Connected":
						self._update_fields({"status": "Connected"})
					return {
						"status": "connected",
						"message": f"Device {self.number} is connected to WhatsApp",
						"device_number": self.number,
						"last_sync": self.last_sync,
					}

				if state in {"qr_ready", "qr_generated"}:
					if self.status != "QR Generated":
						payload = {"status": "QR Generated"}
						qr_data = status_payload.get("qr") or status_payload.get("qr_data")
						if qr_data:
							payload["qr_code"] = qr_data
						self._update_fields(payload)
					return {
						"status": "qr_ready" if state == "qr_ready" else "qr_generated",
						"message": f"QR code generated for {self.number}. Please scan to connect.",
						"device_number": self.number,
						"qr": status_payload.get("qr") or status_payload.get("qr_data"),
					}

				if state in {"error", "not_found"} and self.status != "Connected":
					if self.status != "Disconnected":
						self._update_fields({"status": "Disconnected"})

			if self.status == "Connected":
				return {
					"status": "connected",
					"message": f"Device {self.number} is connected to WhatsApp",
					"device_number": self.number,
					"last_sync": self.last_sync,
				}

			if self.status == "QR Generated":
				return {
					"status": "qr_generated",
					"message": f"QR code generated for {self.number}. Please scan to connect.",
					"device_number": self.number,
				}

			return {
				"status": "disconnected",
				"message": f"Device {self.number} is not connected. Generate QR code to connect.",
				"device_number": self.number,
			}
		except Exception as exc:
			frappe.log_error(f"Error checking connection status: {exc}", "WhatsApp Connection Status")
			return {
				"status": "error",
				"message": f"Failed to check connection status: {exc}",
				"device_number": self.number,
			}

	@frappe.whitelist()
	def check_connection(self):
		"""Baseline connection check hook (legacy)."""
		try:
			settings = frappe.get_doc("WhatsApp Settings")
			if settings.mode != "Unofficial":
				return {
					"status": "not_supported",
					"message": "Connection check only works in Unofficial mode",
				}
			return {"status": "checking", "message": "Connection status check in progress"}
		except Exception as exc:
			frappe.log_error(f"Error checking connection: {exc}", "WhatsApp Connection Check")
			return {"status": "error", "message": "Failed to check connection status"}

	def on_insert(self):
		"""Generate QR code automatically when creating new device."""
		if not self.qr_code:
			self.generate_qr_code()

	@frappe.whitelist()
	def generate_qr_code(self):
		"""Generate QR code for WhatsApp Web authentication."""
		settings = frappe.get_doc("WhatsApp Settings")
		if settings.mode != "Unofficial":
			frappe.msgprint("QR Code generation only works in Unofficial mode")
			return

		try:
			from whatsapp_integration.api.whatsapp_playwright import generate_whatsapp_qr_pw

			frappe.msgprint("Generating WhatsApp QR (Playwright, headless)...")
			result = generate_whatsapp_qr_pw(self.number, timeout=90)
			status = result.get("status")

			if status in {"qr_generated", "qr_ready"}:
				self._update_fields({
					"qr_code": result.get("qr") or result.get("qr_data"),
					"status": "QR Generated",
				})
				frappe.msgprint(f"QR generated (Playwright) for {self.number}. Scan with your phone.")
				return result

			if status == "already_connected":
				self._update_fields({"status": "Connected"})
				frappe.msgprint(f"Device {self.number} is already connected!")
				return result

			if status == "error":
				raise Exception(result.get("message"))

			frappe.log_error(f"Unexpected Playwright QR response: {result}", "WhatsApp Device")
			frappe.msgprint("Playwright QR did not succeed. Falling back to simple QR.")

			from whatsapp_integration.api.whatsapp_simple import generate_simple_qr_code

			simple = generate_simple_qr_code(self.number)
			if simple.get("status") == "qr_generated":
				self._update_fields({
					"qr_code": simple.get("qr"),
					"status": "QR Generated",
				})
				frappe.msgprint("Simple QR generated. Visit https://web.whatsapp.com manually to scan.")
				return simple

			error_msg = simple.get("message", "Failed to generate simple QR code")
			frappe.throw(f"QR generation failed: {error_msg}")
		except Exception as exc:
			frappe.log_error(f"QR Generation Error: {exc}", "WhatsApp QR Generation")
			frappe.throw(f"Error generating QR code: {exc}")

	def test_connection(self):
		"""Test WhatsApp connection status via simple service."""
		try:
			if self.status == "Connected":
				from whatsapp_integration.api.whatsapp_simple import check_simple_session_status

				result = check_simple_session_status(self.number)
				return {"success": True, "message": f"Connection status: {result.get('status')}"}
			return {"success": False, "message": "Device is not connected. Generate QR code first."}
		except Exception as exc:
			frappe.log_error(f"Connection Test Error: {exc}", "WhatsApp Connection Test")
			return {"success": False, "message": f"Connection test failed: {exc}"}

	@frappe.whitelist()
	def sync_status(self):
		"""Sync DocType status with live session status."""
		try:
			try:
				from whatsapp_integration.api.whatsapp_playwright import check_qr_status_pw

				status_payload = check_qr_status_pw(self.number)
			except Exception:
				status_payload = None

			updated = False
			message = None

			if isinstance(status_payload, dict):
				state = status_payload.get("status")
				if state == "connected":
					if self.status != "Connected":
						self._update_fields({"status": "Connected"})
						updated = True
					message = "Device is connected."
				elif state in {"qr_ready", "qr_generated"}:
					if self.status != "QR Generated":
						self._update_fields({"status": "QR Generated"})
						updated = True
					qr_data = status_payload.get("qr") or status_payload.get("qr_data")
					if qr_data and self.qr_code != qr_data:
						self._update_fields({"qr_code": qr_data})
						updated = True
					message = "QR session active. Keep the dialog open."
				elif state in {"error", "not_found"}:
					if self.status != "Connected":
						self._update_fields({"status": "Disconnected"})
						updated = True
					message = f"Status: {state}"

			return {
				"success": True,
				"updated": updated,
				"status": self.status,
				"message": message or "Status synced",
			}
		except Exception as exc:
			frappe.log_error(f"Sync Status Error: {exc}", "WhatsApp Device Sync")
			return {"success": False, "message": f"Failed to sync status: {exc}"}


@frappe.whitelist()
def refresh_qr_code(device_name):
	"""Manual QR code refresh for existing devices using Playwright."""
	try:
		device = frappe.get_doc("WhatsApp Device", device_name)

		from whatsapp_integration.api.whatsapp_playwright import generate_whatsapp_qr_pw

		result = generate_whatsapp_qr_pw(device.number, timeout=90)
		status = result.get("status")

		if status in {"qr_generated", "qr_ready"}:
			frappe.db.set_value(
				"WhatsApp Device",
				device.name,
				{
					"qr_code": result.get("qr") or result.get("qr_data"),
					"status": "QR Generated",
				},
			)
			return {"message": "QR Code refreshed successfully"}

		if status == "already_connected":
			frappe.db.set_value("WhatsApp Device", device.name, {"status": "Connected"})
			return {"message": "Device is already connected"}

		error_msg = result.get("message", "Failed to refresh QR code")
		frappe.throw(f"QR refresh failed: {error_msg}")
	except Exception as exc:
		frappe.log_error(f"QR Refresh Error: {exc}", "WhatsApp QR Refresh")
		return {"error": str(exc)}
