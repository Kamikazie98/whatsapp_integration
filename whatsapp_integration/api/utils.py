import frappe
from frappe.utils import now


def resolve_device_name(session_or_device):
    """Return the WhatsApp Device name for a given session/device id."""
    if not session_or_device:
        return None

    session_value = str(session_or_device).strip()
    if not session_value:
        return None

    if frappe.db.exists("WhatsApp Device", session_value):
        return session_value

    device_name = frappe.db.get_value("WhatsApp Device", {"number": session_value}, "name")
    return device_name


def mark_device_active(device_name, status=None):
    """Update last_sync (and optionally status) on a device."""
    if not device_name or not frappe.db.exists("WhatsApp Device", device_name):
        return

    values = {"last_sync": now()}
    if status:
        values["status"] = status
    frappe.db.set_value("WhatsApp Device", device_name, values)
