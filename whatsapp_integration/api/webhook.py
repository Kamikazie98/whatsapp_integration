import frappe
from frappe.utils import now
from whatsapp_integration.api.utils import mark_device_active, resolve_device_name, find_party_by_number

@frappe.whitelist(allow_guest=True)
def receive_message():
    """Unified webhook endpoint for both Official and Unofficial modes"""
    data = frappe.request.get_json() if frappe.request.method == "POST" else frappe.form_dict
    settings = frappe.get_doc("WhatsApp Settings")

    if frappe.request.method == "GET" and settings.mode == "Official":
        # Meta webhook verification handshake
        mode = frappe.form_dict.get("hub.mode")
        token = frappe.form_dict.get("hub.verify_token")
        challenge = frappe.form_dict.get("hub.challenge")
        if token == settings.verify_token:
            return challenge
        return "Invalid token", 403

    if frappe.request.method == "POST":
        if settings.mode == "Official":
            # Meta webhook payload
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    messages = change["value"].get("messages", [])
                    for msg in messages:
                        number = msg.get("from")
                        text = (msg.get("text") or {}).get("body") or ""
                        timestamp = msg.get("timestamp")
                        device_name = _log_incoming_message(number, text, timestamp=timestamp)
                        _publish_incoming_notification(None, number, text, device_name, timestamp)

        else:
            # Unofficial webhook payload
            number = data.get("from")
            text = data.get("text") or ""
            session_id = data.get("session")
            timestamp = data.get("timestamp")
            device_name = _log_incoming_message(number, text, session_id=session_id, timestamp=timestamp)
            _publish_incoming_notification(session_id, number, text, device_name, timestamp)

        return {"status": "ok"}

def _log_incoming_message(number, message, session_id=None, timestamp=None):
    device_name = resolve_device_name(session_id)
    
    # Auto-detect party type and name based on phone number
    party_type, party_name = find_party_by_number(number)
    
    log_doc = frappe.get_doc({
        "doctype": "WhatsApp Message Log",
        "number": number,
        "message": message,
        "direction": "In",
        "status": "Received",
        "device": device_name,
        "party_type": party_type,
        "party_name": party_name,
        "sent_time": timestamp or now()
    })
    log_doc.insert(ignore_permissions=True)

    if device_name:
        mark_device_active(device_name, status="Connected")

    return device_name

def _publish_incoming_notification(session_id, number, message, device_name, timestamp):
    payload = {
        "session": session_id,
        "device": device_name,
        "number": number,
        "message": message,
        "timestamp": timestamp or now()
    }
    frappe.publish_realtime(
        "whatsapp_incoming_message",
        payload,
        doctype="WhatsApp Device",
        docname=device_name or session_id or "WhatsApp Device",
        after_commit=True,
    )
