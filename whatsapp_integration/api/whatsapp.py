import frappe
from frappe.utils import now
from whatsapp_integration.api.utils import mark_device_active, resolve_device_name
from whatsapp_integration.api.whatsapp_official import send_official
from whatsapp_integration.api.whatsapp_unofficial import send_unofficial

@frappe.whitelist()
def send_whatsapp_message(number, message, session=None):
    """Unified entry point for sending WhatsApp messages"""
    settings = frappe.get_doc("WhatsApp Settings")
    device_name = resolve_device_name(session)
    
    # Log the message attempt
    log_doc = frappe.get_doc({
        "doctype": "WhatsApp Message Log",
        "number": number,
        "message": message,
        "direction": "Out",
        "status": "Sending",
        "device": device_name
    })
    log_doc.insert(ignore_permissions=True)
    
    try:
        if settings.mode == "Official":
            result = send_official(number, message)
            session_used = session
        else:
            result = send_unofficial(number, message, session_id=session or device_name)
            session_used = result.get("session") or session

        resolved_device = resolve_device_name(session_used)
        if resolved_device and not log_doc.device:
            log_doc.device = resolved_device
        log_doc.status = "Sent"
        log_doc.sent_time = now()
        log_doc.save(ignore_permissions=True)

        if isinstance(result, dict) and resolved_device and not result.get("session"):
            result["session"] = resolved_device

        if resolved_device:
            mark_device_active(resolved_device, status="Connected")
        return result
        
    except Exception as e:
        log_doc.status = "Failed"
        log_doc.error_message = str(e)
        log_doc.save(ignore_permissions=True)
        raise e
