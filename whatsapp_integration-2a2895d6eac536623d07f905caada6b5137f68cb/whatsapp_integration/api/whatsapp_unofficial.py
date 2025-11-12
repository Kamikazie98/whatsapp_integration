import frappe

def send_unofficial(number, message):
    """Send message via Python WhatsApp service"""
    try:
        from whatsapp_integration.api.whatsapp_python import send_message
        
        # Use first available session or default session name
        session_id = number  # Use phone number as session ID
        result = send_message(session_id, number, message)
        
        if result.get("success"):
            return result
        else:
            raise Exception(result.get("error", "Failed to send message"))
            
    except Exception as e:
        frappe.log_error(f"Unofficial send error: {str(e)}", "WhatsApp Unofficial Send")
        raise Exception(f"Failed to send message: {str(e)}")

@frappe.whitelist()
def check_device_status(session_id):
    """Check if WhatsApp device is connected using Python service"""
    try:
        from whatsapp_integration.api.whatsapp_python import check_session_status
        result = check_session_status(session_id)
        return {"status": result.get("status", "Unknown")}
        
    except Exception as e:
        frappe.log_error(f"Status check error: {str(e)}", "WhatsApp Status Check")
        return {"status": "Error", "message": str(e)}
