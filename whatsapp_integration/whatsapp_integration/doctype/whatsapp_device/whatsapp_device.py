import frappe
from frappe.model.document import Document

class WhatsAppDevice(Document):
    @frappe.whitelist()
    def mark_connected(self):
        """Manually mark device as connected (for testing)"""
        try:
            from whatsapp_integration.api.whatsapp_simple import set_session_connected
            result = set_session_connected(self.number)
            if result.get("success"):
                self.status = "Connected"
                self.save()
                return {"success": True, "message": "Device marked as connected"}
            else:
                return {"success": False, "message": "Failed to mark device as connected"}
        except Exception as e:
            frappe.log_error(f"Mark Connected Error: {str(e)}", "WhatsApp Mark Connected")
            return {"success": False, "message": f"Error: {str(e)}"}

    @frappe.whitelist()
	def check_connection_status(self):
		"""Check WhatsApp connection status using Playwright profile."""
		try:
			status_payload = None
			try:
				from whatsapp_integration.api.whatsapp_playwright import check_qr_status_pw
				status_payload = check_qr_status_pw(self.number)
			except Exception:
				status_payload = None

			if isinstance(status_payload, dict):
				st = status_payload.get("status")
				if st == "connected":
					if self.status != "Connected":
						self.status = "Connected"
						self.save()
					return {
						"status": "connected",
						"message": f"Device {self.number} is connected to WhatsApp",
						"device_number": self.number,
						"last_sync": self.last_sync
					}
				if st in ("qr_ready", "qr_generated"):
					if self.status != "QR Generated":
						self.status = "QR Generated"
						qr = status_payload.get("qr") or status_payload.get("qr_data")
						if qr:
							self.qr_code = qr
						self.save()
					return {
						"status": "qr_ready" if st == "qr_ready" else "qr_generated",
						"message": f"QR code generated for {self.number}. Please scan to connect.",
						"device_number": self.number,
						"qr": status_payload.get("qr") or status_payload.get("qr_data")
					}
				if st in ("error", "not_found") and self.status != "Connected":
					if self.status != "Disconnected":
						self.status = "Disconnected"
						self.save()

			if self.status == "Connected":
				return {
					"status": "connected",
					"message": f"Device {self.number} is connected to WhatsApp",
					"device_number": self.number,
					"last_sync": self.last_sync
				}
			elif self.status == "QR Generated":
				return {
					"status": "qr_generated",
					"message": f"QR code generated for {self.number}. Please scan to connect.",
					"device_number": self.number
				}
			else:
				return {
					"status": "disconnected",
					"message": f"Device {self.number} is not connected. Generate QR code to connect.",
					"device_number": self.number
				}

		except Exception as e:
			frappe.log_error(f"Error checking connection status: {str(e)}", "WhatsApp Connection Status")
			return {
				"status": "error",
				"message": f"Failed to check connection status: {str(e)}",
				"device_number": self.number
			}

    @frappe.whitelist()
    def check_connection(self):
        """Check the connection status of the WhatsApp device"""
        try:
            settings = frappe.get_doc("WhatsApp Settings")
            
            if settings.mode != "Unofficial":
                return {
                    "status": "not_supported",
                    "message": "Connection check only works in Unofficial mode"
                }
            
            # For now, return a basic status - we can enhance this later
            return {
                "status": "checking",
                "message": "Connection status check in progress"
            }
            
        except Exception as e:
            frappe.log_error(f"Error checking connection: {str(e)}", "WhatsApp Connection Check")
            return {
                "status": "error",
                "message": "Failed to check connection status"
            }
    
    def on_insert(self):
        """Generate QR code automatically when creating new device"""
        if not self.qr_code:
            self.generate_qr_code()
    
    @frappe.whitelist()
    def generate_qr_code(self):
        """Generate QR code for WhatsApp Web authentication using Python service"""
        settings = frappe.get_doc("WhatsApp Settings")
        
        if settings.mode != "Unofficial":
            frappe.msgprint("QR Code generation only works in Unofficial mode")
            return
        
		try:
			from whatsapp_integration.api.whatsapp_playwright import generate_whatsapp_qr_pw
			frappe.msgprint("Generating WhatsApp QR (Playwright, headless)...")
			result = generate_whatsapp_qr_pw(self.number, timeout=90)
			status = result.get("status")
			if status in ("qr_generated", "qr_ready"):
				self.qr_code = result.get("qr") or result.get("qr_data")
				self.status = "QR Generated"
				self.save()
				frappe.msgprint(f"QR generated (Playwright) for {self.number}. Scan with your phone.")
				return result
			if status == "already_connected":
				self.status = "Connected"
				self.save()
				frappe.msgprint(f"Device {self.number} is already connected!")
				return result
			if status == "error":
				raise Exception(result.get("message"))

			frappe.log_error(f"Unexpected Playwright QR response: {result}", "WhatsApp Device")
			frappe.msgprint("Playwright QR did not succeed. Falling back to simple QR.")

			from whatsapp_integration.api.whatsapp_simple import generate_simple_qr_code
			simple = generate_simple_qr_code(self.number)
			if simple.get("status") == "qr_generated":
				self.qr_code = simple.get("qr")
				self.status = "QR Generated"
				self.save()
				frappe.msgprint("Simple QR generated. Visit https://web.whatsapp.com manually to scan.")
				return simple
			error_msg = simple.get("message", "Failed to generate simple QR code")
			frappe.throw(f"QR generation failed: {error_msg}")

		except Exception as e:
			frappe.log_error(f"QR Generation Error: {str(e)}", "WhatsApp QR Generation")
			frappe.throw(f"Error generating QR code: {str(e)}")

    def test_connection(self):
        """Test WhatsApp connection status"""
        try:
            if self.status == "Connected":
                from whatsapp_integration.api.whatsapp_simple import check_simple_session_status
                result = check_simple_session_status(self.number)
                return {"success": True, "message": f"Connection status: {result.get('status')}"}
            else:
                return {"success": False, "message": "Device is not connected. Generate QR code first."}
        except Exception as e:
            frappe.log_error(f"Connection Test Error: {str(e)}", "WhatsApp Connection Test")
            return {"success": False, "message": f"Connection test failed: {str(e)}"}

    @frappe.whitelist()
    def sync_status(self):
        """Sync DocType status with real QR/driver status.

        Mapping:
        - connected  -> Status = Connected
        - qr_ready   -> Status = QR Generated and update qr_code
        - starting   -> Status = QR Generated (UI continues polling)
        - not_found/error -> Status = Disconnected (if not already Connected)
        """
		try:
			status_payload = None
			try:
				from whatsapp_integration.api.whatsapp_playwright import check_qr_status_pw
				status_payload = check_qr_status_pw(self.number)
			except Exception:
				status_payload = None

			updated = False
			msg = None

			if isinstance(status_payload, dict):
				st = status_payload.get("status")
				if st == "connected":
					if self.status != "Connected":
						self.status = "Connected"
						updated = True
					msg = "Device is connected."
				elif st in ("qr_ready", "qr_generated"):
					if self.status != "QR Generated":
						self.status = "QR Generated"
						updated = True
					qr = status_payload.get("qr") or status_payload.get("qr_data")
					if qr and self.qr_code != qr:
						self.qr_code = qr
						updated = True
					msg = "QR session active. Keep the dialog open."
				elif st in ("error", "not_found"):
					if self.status != "Connected":
						self.status = "Disconnected"
						updated = True
					msg = f"Status: {st}"

			if updated:
				self.save()

			return {
				"success": True,
				"updated": updated,
				"status": self.status,
				"message": msg or "Status synced"
			}

		except Exception as e:
			frappe.log_error(f"Sync Status Error: {str(e)}", "WhatsApp Device Sync")
			return {"success": False, "message": f"Failed to sync status: {str(e)}"}

    @frappe.whitelist()
    def mark_connected(self):
        """Manually mark device as connected (for testing)"""
        try:
            from whatsapp_integration.api.whatsapp_simple import set_session_connected
            result = set_session_connected(self.number)
            if result.get("success"):
                self.status = "Connected"
                self.save()
                return {"success": True, "message": "Device marked as connected"}
            else:
                return {"success": False, "message": "Failed to mark device as connected"}
        except Exception as e:
            frappe.log_error(f"Mark Connected Error: {str(e)}", "WhatsApp Mark Connected")
            return {"success": False, "message": f"Error: {str(e)}"}

@frappe.whitelist()
def refresh_qr_code(device_name):
    """Manual QR code refresh for existing devices using Python service"""
    try:
        device = frappe.get_doc("WhatsApp Device", device_name)
        
        from whatsapp_integration.api.whatsapp_playwright import generate_whatsapp_qr_pw
        result = generate_whatsapp_qr_pw(device.number, timeout=90)
        
        if result.get("status") in ("qr_generated", "qr_ready"):
            device.qr_code = result.get("qr") or result.get("qr_data")
            device.status = "QR Generated"
            device.save()
            return {"message": "QR Code refreshed successfully"}
        if result.get("status") == "already_connected":
            device.status = "Connected"
            device.save()
            return {"message": "Device is already connected"}
        
        error_msg = result.get("message", "Failed to refresh QR code")
        frappe.throw(f"QR refresh failed: {error_msg}")
            
    except Exception as e:
        frappe.log_error(f"QR Refresh Error: {str(e)}", "WhatsApp QR Refresh")
        return {"error": str(e)}
