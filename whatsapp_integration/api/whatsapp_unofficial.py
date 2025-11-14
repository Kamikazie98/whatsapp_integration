import os
import requests
import frappe
from whatsapp_integration.api.utils import mark_device_active, resolve_device_name

def _get_node_base_url():
    env_url = os.getenv("WHATSAPP_NODE_URL")
    if env_url:
        return env_url.rstrip("/")
    try:
        settings = frappe.get_doc("WhatsApp Settings")
        node_url = (getattr(settings, 'nodejs_url', '') or '').strip()
        if node_url:
            return node_url.rstrip("/")
    except Exception:
        pass
    return "http://localhost:8001"

def _pick_connected_device():
    device = frappe.get_list(
        "WhatsApp Device",
        filters={"status": "Connected"},
        fields=["name", "number"],
        limit=1,
    )
    return device[0] if device else None

def send_unofficial(number, message, session_id=None):
    """Send message via Node.js WhatsApp service using a connected device session.

    `number` is the recipient. We auto-select a connected WhatsApp Device as session.
    """
    try:
        device_name = None
        session_to_use = None

        if session_id:
            device_name = resolve_device_name(session_id)
            if not device_name:
                raise Exception(f"Requested session '{session_id}' was not found.")
            device_status = frappe.db.get_value("WhatsApp Device", device_name, "status")
            if device_status != "Connected":
                raise Exception(f"Session '{session_id}' is not connected. Please scan the QR code again.")
            session_to_use = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_id
        else:
            device = _pick_connected_device()
            if not device:
                pending = frappe.get_list(
                    "WhatsApp Device",
                    filters={"status": ["in", ["QR Generated", "Disconnected"]]},
                    fields=["name", "number"],
                    limit=1,
                )
                if not pending:
                    raise Exception("No WhatsApp device is connected. Generate QR and connect a device first.")
                raise Exception("WhatsApp device not connected yet. Please scan QR on a device and try again.")
            session_to_use = device["number"]
            device_name = device["name"]

        base = _get_node_base_url()
        payload = {"session": session_to_use, "to": number, "message": message}
        resp = requests.post(f"{base}/sendMessage", json=payload, timeout=20)
        if resp.status_code != 200:
            raise Exception(f"Node send error: HTTP {resp.status_code} {resp.text}")
        data = resp.json()
        if not data.get("success"):
            raise Exception(data.get("error") or "Unknown send error")
        data.setdefault("session", session_to_use)

        resolved_name = resolve_device_name(data.get("session"))
        if resolved_name:
            mark_device_active(resolved_name, status="Connected")

        return data
    except Exception as e:
        frappe.log_error(f"Unofficial send error (Node): {str(e)}", "WhatsApp Unofficial Send")
        raise Exception(f"Failed to send message: {str(e)}")

@frappe.whitelist()
def check_device_status(session_id):
    """Check if WhatsApp device is connected using Node service"""
    try:
        base = _get_node_base_url()
        resp = requests.get(f"{base}/status/{session_id}", timeout=10)
        if resp.status_code != 200:
            return {"status": "Error", "message": f"HTTP {resp.status_code}"}
        data = resp.json()
        return {"status": data.get("status", "Unknown")}
    except Exception as e:
        frappe.log_error(f"Status check error (Node): {str(e)}", "WhatsApp Status Check")
        return {"status": "Error", "message": str(e)}
