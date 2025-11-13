import frappe
from whatsapp_integration.api.whatsapp_official import send_official
from whatsapp_integration.api.whatsapp_unofficial import send_unofficial

@frappe.whitelist()
def send_whatsapp_message(number, message, device=None):
    """Unified entry point for sending WhatsApp messages"""
    settings = frappe.get_doc("WhatsApp Settings")
    # Safely get default_device to avoid AttributeError if the field doesn't exist
    device_name = device or getattr(settings, "default_device", None)

    if not device_name:
        # If no device is specified, automatically find one that is connected
        device_name = frappe.db.get_value("WhatsApp Device", {"status": "Connected"}, "name")
        if not device_name:
            frappe.throw("No device specified and no connected WhatsApp device found.")

    # Log the message attempt
    log_doc = frappe.get_doc({
        "doctype": "WhatsApp Message Log",
        "number": number,
        "message": message,
        "direction": "Out",
        "status": "Sending",
        "device": device_name,
    })
    log_doc.insert(ignore_permissions=True)

    try:
        if settings.mode == "Official":
            result = send_official(number, message)
        else:
            # Pre-send check for Unofficial mode
            device_doc = frappe.get_doc("WhatsApp Device", device_name)
            if device_doc.status != "Connected":
                raise frappe.ValidationError(
                    f"Device '{device_name}' is not connected. Current status: {device_doc.status}."
                )
            result = send_unofficial(device_name, number, message)

        log_doc.status = "Sent"
        log_doc.save(ignore_permissions=True)
        return result
        
    except Exception as e:
        log_doc.status = "Failed"
        log_doc.error_message = str(e)
        log_doc.save(ignore_permissions=True)
        # The main frappe logger will still capture this traceback
        raise e
