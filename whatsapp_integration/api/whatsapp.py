import frappe
from whatsapp_integration.api.whatsapp_official import send_official
from whatsapp_integration.api.whatsapp_unofficial import send_unofficial

@frappe.whitelist()
def send_whatsapp_message(number, message):
    """Unified entry point for sending WhatsApp messages"""
    settings = frappe.get_doc("WhatsApp Settings")
    
    # Log the message attempt
    log_doc = frappe.get_doc({
        "doctype": "WhatsApp Message Log",
        "number": number,
        "message": message,
        "direction": "Out",
        "status": "Sending"
    })
    log_doc.insert(ignore_permissions=True)
    
    try:
        if settings.mode == "Official":
            result = send_official(number, message)
        else:
            result = send_unofficial(number, message)
        
        log_doc.status = "Sent"
        log_doc.save(ignore_permissions=True)
        return result
        
    except Exception as e:
        log_doc.status = "Failed"
        log_doc.error_message = str(e)
        log_doc.save(ignore_permissions=True)
        raise e
