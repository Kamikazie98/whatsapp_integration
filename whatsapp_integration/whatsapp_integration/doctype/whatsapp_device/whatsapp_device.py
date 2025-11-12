import os
import requests
import frappe
from frappe.model.document import Document


def _get_node_base_url():
    """Resolve Node.js WhatsApp service base URL.
    Priority: env `WHATSAPP_NODE_URL` > Settings.nodejs_url > default.
    """
    env_url = os.getenv("WHATSAPP_NODE_URL")
    if env_url:
        return env_url.rstrip("/")

    try:
        settings = frappe.get_doc("WhatsApp Settings")
        node_url = (settings.nodejs_url or "").strip()
        if node_url:
            return node_url.rstrip("/")
    except Exception:
        pass

    return "http://localhost:8001"

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
        """Check actual WhatsApp connection status (not just QR session)"""
        try:
            # Check if we have a real WhatsApp connection
            if self.status == "Connected":
                # For now, return connected status
                # In a real implementation, you'd ping WhatsApp Web or check session validity
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
        """Generate QR code using Node.js WhatsApp service (Unofficial)."""
        settings = frappe.get_doc("WhatsApp Settings")

        if settings.mode != "Unofficial":
            frappe.msgprint("QR Code generation only works in Unofficial mode")
            return

        base_url = _get_node_base_url()
        session_id = self.number

        try:
            resp = requests.get(f"{base_url}/qr/{session_id}", timeout=20)
            if resp.status_code != 200:
                raise Exception(f"Node service error: HTTP {resp.status_code}")
            data = resp.json()
            qr = data.get("qr")
            if not qr:
                raise Exception("QR not returned by Node service")

            self.qr_code = qr
            self.status = "QR Generated"
            self.save()
            frappe.msgprint(f"QR generated via Node for {self.number}. Scan with WhatsApp.")
            return {"status": "qr_generated", "qr": qr, "session": session_id}

        except Exception as e:
            frappe.log_error(f"Node QR Generation Failed: {str(e)}", "WhatsApp Device")
            # Fallback to previous simple QR to avoid blocking user
            try:
                from whatsapp_integration.api.whatsapp_simple import generate_simple_qr_code
                result = generate_simple_qr_code(self.number)
                if result.get("status") == "qr_generated":
                    self.qr_code = result.get("qr")
                    self.status = "QR Generated"
                    self.save()
                    frappe.msgprint("Fallback simple QR generated. Open https://web.whatsapp.com to scan.")
                    return result
            except Exception:
                pass

            frappe.throw(f"Error generating QR via Node: {str(e)}")

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
    """Manual QR code refresh for existing devices using Node service (Unofficial)."""
    try:
        device = frappe.get_doc("WhatsApp Device", device_name)
        base_url = _get_node_base_url()
        session_id = device.number

        resp = requests.get(f"{base_url}/qr/{session_id}", timeout=20)
        if resp.status_code != 200:
            frappe.throw(f"Node service error: HTTP {resp.status_code}")
        data = resp.json()
        qr = data.get("qr")
        if not qr:
            frappe.throw("QR not returned by Node service")

        device.qr_code = qr
        device.status = "Disconnected"
        device.save()
        return {"message": "QR Code refreshed successfully via Node"}
        
    except Exception as e:
        frappe.log_error(f"QR Refresh Error: {str(e)}", "WhatsApp QR Refresh")
        return {"error": str(e)}
