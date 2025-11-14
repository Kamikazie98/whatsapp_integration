import frappe
from frappe import _
from frappe.utils import now

from whatsapp_integration.api.utils import mark_device_active, resolve_device_name
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
