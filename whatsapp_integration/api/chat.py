import frappe
from frappe import _
from frappe.utils import now
from urllib.parse import urlparse

from whatsapp_integration.api.utils import mark_device_active, resolve_device_name, find_party_by_number
from whatsapp_integration.api.whatsapp_unofficial import send_unofficial


def _ensure_unofficial_mode():
    settings = frappe.get_doc("WhatsApp Settings")
    if settings.mode != "Unofficial":
        frappe.throw(_("WhatsApp chat console works only in Unofficial mode."))
    return settings


def _serialize_log(doc, extra=None):
    payload = {
        "name": doc.name,
        "number": doc.number,
        "message": doc.message,
        "direction": doc.direction,
        "status": doc.status,
        "device": doc.device,
        "sent_time": doc.sent_time,
        "error_message": doc.error_message,
    }
    if extra:
        payload.update(extra)
    return payload


@frappe.whitelist()
def send_chat_message(number, message, session=None):
    """Send a WhatsApp message using the Node (Unofficial) service and log it."""
    _ensure_unofficial_mode()

    number = (number or "").strip()
    message = (message or "").strip()
    if not number or not message:
        frappe.throw(_("Phone number and message are required."))

    session_value = (session or "").strip() or None
    device_name = resolve_device_name(session_value)
    
    # Auto-detect party type and name based on phone number
    party_type, party_name = find_party_by_number(number)

    log_doc = frappe.get_doc({
        "doctype": "WhatsApp Message Log",
        "number": number,
        "message": message,
        "direction": "Out",
        "status": "Sending",
        "device": device_name,
        "party_type": party_type,
        "party_name": party_name,
    })
    log_doc.insert(ignore_permissions=True)

    try:
        result = send_unofficial(number, message, session_id=session_value or device_name)
        session_used = (result.get("session") if isinstance(result, dict) else None) or session_value or device_name
        resolved_device = resolve_device_name(session_used)
        if resolved_device and not log_doc.device:
            log_doc.device = resolved_device
        log_doc.status = "Sent"
        log_doc.sent_time = now()
        log_doc.save(ignore_permissions=True)

        active_device = log_doc.device or resolved_device
        if active_device:
            mark_device_active(active_device, status="Connected")

        payload = _serialize_log(log_doc, {"session": session_used})
        frappe.publish_realtime(
            "whatsapp_chat_update",
            payload,
            doctype="WhatsApp Device",
            docname=active_device or session_used or "WhatsApp Device",
            after_commit=True,
        )
        return {"message": _("Message sent"), "log": payload, "session": session_used}

    except Exception as exc:
        log_doc.status = "Failed"
        log_doc.error_message = str(exc)
        log_doc.save(ignore_permissions=True)
        payload = _serialize_log(log_doc, {"session": session_value or device_name})
        frappe.publish_realtime(
            "whatsapp_chat_update",
            payload,
            doctype="WhatsApp Device",
            docname=log_doc.device or session_value or "WhatsApp Device",
            after_commit=True,
        )
        raise


@frappe.whitelist()
def get_chat_history(number, limit=50, before=None):
    """Return WhatsApp Message Log entries for a phone number (ascending)."""
    number = (number or "").strip()
    if not number:
        return {"messages": []}

    limit = int(limit or 50)
    limit = max(1, min(limit, 200))

    filters = {"number": number}
    if before:
        filters["sent_time"] = ["<", before]

    rows = frappe.get_all(
        "WhatsApp Message Log",
        filters=filters,
        fields=["name", "number", "message", "direction", "status", "device", "sent_time", "error_message"],
        order_by="sent_time desc",
        limit=limit,
    )
    rows.reverse()
    oldest = rows[0]["sent_time"] if rows else None
    return {"messages": rows, "oldest": oldest}


@frappe.whitelist()
def list_recent_numbers(search=None, limit=20):
    """Return recently active numbers to populate the chat sidebar."""
    limit = int(limit or 20)
    limit = max(1, min(limit, 100))
    search = (search or "").strip()

    conditions = ""
    values = {"limit": limit}
    if search:
        conditions = "AND number LIKE %(search)s"
        values["search"] = f"%{search}%"

    results = frappe.db.sql(
        f"""
        SELECT number, MAX(sent_time) AS last_time
        FROM `tabWhatsApp Message Log`
        WHERE IFNULL(number, '') != '' {conditions}
        GROUP BY number
        ORDER BY last_time DESC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )
    return {"numbers": results}


@frappe.whitelist()
def get_available_devices(only_connected=False):
    """Return WhatsApp devices to populate the session selector."""
    filters = {}
    if frappe.utils.cint(only_connected):
        filters["status"] = "Connected"

    devices = frappe.get_all(
        "WhatsApp Device",
        filters=filters,
        fields=["name", "number", "status", "last_sync"],
        order_by="modified desc",
    )
    return {"devices": devices}


@frappe.whitelist()
def load_whatsapp_chats(session=None):
    """Load chats from WhatsApp for a specific session."""
    _ensure_unofficial_mode()
    
    import requests
    from whatsapp_integration.api.whatsapp_unofficial import _get_node_base_url
    
    session_value = (session or "").strip() or None
    device_name = resolve_device_name(session_value)
    
    # Use device number as session if available
    if device_name:
        session_to_use = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_value or device_name
    else:
        session_to_use = session_value or "default"
    
    base_url = _get_node_base_url()
    
    # Combine groups from WhatsApp and individual chats from database
    chats = []
    
    try:
        resp = requests.get(f"{base_url}/chats/{session_to_use}", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success") and data.get("chats"):
            chats.extend(data.get("chats", []))
    except Exception as e:
        frappe.log_error(f"Failed to load WhatsApp groups: {str(e)}", "WhatsApp Load Chats")
    
    # Get individual chats from message history (database)
    filters = {}
    if device_name:
        filters["device"] = device_name
    
    # Get unique numbers from recent messages
    recent_chats = frappe.db.sql("""
        SELECT DISTINCT number, MAX(sent_time) as last_time
        FROM `tabWhatsApp Message Log`
        WHERE device = %(device)s OR %(device)s IS NULL
        GROUP BY number
        ORDER BY last_time DESC
        LIMIT 100
    """, {"device": device_name or ""}, as_dict=True)
    
    for chat in recent_chats:
        # Check if already in groups list
        if not any(c.get("number") == chat.number for c in chats):
            party_type, party_name = find_party_by_number(chat.number)
            chats.append({
                "id": f"{chat.number}@s.whatsapp.net",
                "number": chat.number,
                "name": party_name or chat.number,
                "isGroup": False,
                "profilePicture": None,
                "lastTime": chat.last_time,
                "partyType": party_type,
                "partyName": party_name,
            })
    
    return {"success": True, "chats": chats}


@frappe.whitelist()
def load_whatsapp_contacts(session=None):
    """Load contacts from WhatsApp for a specific session."""
    _ensure_unofficial_mode()
    
    import requests
    from whatsapp_integration.api.whatsapp_unofficial import _get_node_base_url
    
    session_value = (session or "").strip() or None
    device_name = resolve_device_name(session_value)
    
    # Use device number as session if available
    if device_name:
        session_to_use = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_value or device_name
    else:
        session_to_use = session_value or "default"
    
    base_url = _get_node_base_url()
    
    # Since WhatsApp Web doesn't expose full contact list, use message history
    contacts = []
    
    try:
        resp = requests.get(f"{base_url}/contacts/{session_to_use}", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("success") and data.get("contacts"):
            contacts.extend(data.get("contacts", []))
    except Exception as e:
        frappe.log_error(f"Failed to load WhatsApp contacts: {str(e)}", "WhatsApp Load Contacts")
    
    # Get contacts from message history (database) - exclude groups
    filters = {}
    if device_name:
        filters["device"] = device_name
    
    # Get unique numbers from recent messages (individual chats only, not groups)
    recent_contacts = frappe.db.sql("""
        SELECT DISTINCT number, MAX(sent_time) as last_time
        FROM `tabWhatsApp Message Log`
        WHERE (device = %(device)s OR %(device)s IS NULL)
        AND number NOT LIKE '%@g.us'
        GROUP BY number
        ORDER BY last_time DESC
        LIMIT 200
    """, {"device": device_name or ""}, as_dict=True)
    
    for contact in recent_contacts:
        # Check if already in contacts list
        if not any(c.get("number") == contact.number for c in contacts):
            party_type, party_name = find_party_by_number(contact.number)
            contacts.append({
                "id": f"{contact.number}@s.whatsapp.net",
                "number": contact.number,
                "name": party_name or contact.number,
                "profilePicture": None,
                "lastTime": contact.last_time,
                "partyType": party_type,
                "partyName": party_name,
            })
    
    return {"success": True, "contacts": contacts}


@frappe.whitelist()
def load_whatsapp_messages(session=None, jid=None, limit=50):
    """Load messages from WhatsApp for a specific chat."""
    _ensure_unofficial_mode()
    
    import requests
    from whatsapp_integration.api.whatsapp_unofficial import _get_node_base_url
    
    if not jid:
        frappe.throw(_("Chat ID (JID) is required."))
    
    session_value = (session or "").strip() or None
    device_name = resolve_device_name(session_value)
    
    # Use device number as session if available
    if device_name:
        session_to_use = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_value or device_name
    else:
        session_to_use = session_value or "default"
    
    base_url = _get_node_base_url()
    try:
        resp = requests.get(f"{base_url}/messages/{session_to_use}/{jid}?limit={limit}", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data
    except Exception as e:
        frappe.log_error(f"Failed to load WhatsApp messages: {str(e)}", "WhatsApp Load Messages")
        frappe.throw(_("Failed to load messages from WhatsApp: {error}").format(error=str(e)))


@frappe.whitelist()
def get_websocket_url():
    """Return the WebSocket endpoint exposed by the Node service for live chats."""
    _ensure_unofficial_mode()
    
    from whatsapp_integration.api.whatsapp_unofficial import _get_node_base_url
    
    base = _get_node_base_url()
    parsed = urlparse(base)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    if not netloc:
        frappe.throw(_("Invalid Node service URL"))
    ws_scheme = "wss" if scheme == "https" else "ws"
    base_path = "192.168.85.167:8001"
    suffix = f"{base_path}/ws/chat" 
    return {"url": f"{ws_scheme}://{netloc}{suffix}"}


@frappe.whitelist()
def resolve_node_session(session=None):
    """Return the Node.js session identifier for a selected WhatsApp device."""
    _ensure_unofficial_mode()

    session_value = (session or "").strip() or None
    device_name = resolve_device_name(session_value)
    if device_name:
        resolved = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_value or device_name
    else:
        resolved = session_value or "default"
    return {"session": resolved, "device": device_name}
