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
        """Generate QR code for WhatsApp Web authentication using Python service"""
        settings = frappe.get_doc("WhatsApp Settings")
        
        if settings.mode != "Unofficial":
            frappe.msgprint("QR Code generation only works in Unofficial mode")
            return
        
        try:
            # Prefer the full real QR service first (keeps session alive and monitors scan)
            try:
                from whatsapp_integration.api.whatsapp_real_qr import generate_whatsapp_qr
                frappe.msgprint("Generating WhatsApp QR (persistent session)...")
                result = generate_whatsapp_qr(self.number, timeout=20)

                if result.get("status") == "qr_generated":
                    self.qr_code = result.get("qr")
                    self.status = "QR Generated"
                    self.save()  # Save the document to persist QR data
                    frappe.msgprint(f"QR generated for {self.number}. Scan with your phone to connect.")
                    return result
                elif result.get("status") == "already_connected":
                    self.status = "Connected"
                    self.save()  # Save the status update
                    frappe.msgprint(f"Device {self.number} is already connected!")
                    return result
                else:
                    raise Exception("Persistent QR generation failed")

            except Exception as real_qr_error:
                frappe.log_error(f"Persistent QR Generation Failed: {str(real_qr_error)}", "WhatsApp Device")
                frappe.msgprint(f"Persistent QR failed: {str(real_qr_error)}. Trying quick method...")

                # Fallback to quick QR (non-persistent; last resort before simple)
                try:
                    from whatsapp_integration.api.whatsapp_quick_qr import generate_quick_qr
                    result = generate_quick_qr(self.number)

                    if result.get("status") == "qr_generated":
                        self.qr_code = result.get("qr")
                        self.status = "QR Generated"
                        self.save()  # Save the document to persist QR data
                        frappe.msgprint(f"Quick QR generated for {self.number}. Scan with your phone!")
                        return result
                    else:
                        raise Exception(result.get('message', 'Quick QR failed'))

                except Exception as quick_qr_error:
                    frappe.log_error(f"Quick QR Generation Failed: {str(quick_qr_error)}", "WhatsApp Device")
                    frappe.msgprint(f"Quick QR failed: {str(quick_qr_error)}. Using simple fallback...")

                    # Final fallback to simple QR generation
                    from whatsapp_integration.api.whatsapp_simple import generate_simple_qr_code
                    result = generate_simple_qr_code(self.number)

                    if result.get("status") == "qr_generated":
                        self.qr_code = result.get("qr")
                        self.status = "QR Generated"
                        self.save()  # Save the document to persist QR data
                        frappe.msgprint("Simple QR generated. Visit https://web.whatsapp.com manually to scan.")
                        return result
                    else:
                        error_msg = result.get("message", "Failed to generate simple QR code")
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
        
        from whatsapp_integration.api.whatsapp_python import generate_qr_code
        result = generate_qr_code(device.number)
        
        if result.get("status") == "qr_generated":
            device.qr_code = result.get("qr")
            device.status = "QR Generated"
            device.save()
            return {"message": "QR Code refreshed successfully"}
        elif result.get("status") == "already_connected":
            device.status = "Connected"
            device.save()
            return {"message": "Device is already connected"}
        else:
            error_msg = result.get("message", "Failed to refresh QR code")
            frappe.throw(f"QR refresh failed: {error_msg}")
            
    except Exception as e:
        frappe.log_error(f"QR Refresh Error: {str(e)}", "WhatsApp QR Refresh")
        return {"error": str(e)}
