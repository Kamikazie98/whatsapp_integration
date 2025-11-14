import logging
import os
import requests
import frappe
from whatsapp_integration.api.utils import mark_device_active, resolve_device_name

logger = logging.getLogger(__name__)

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
    # Use explicit loopback IP instead of hostname to avoid Docker/host resolution issues
    return "http://127.0.0.1:8001"

def _fetch_node_session_status(session_id):
    """Return (status, error) for a Node session."""
    if not session_id:
        return None, "missing_session"

    base = _get_node_base_url()
    try:
        resp = requests.get(f"{base}/status/{session_id}", timeout=10)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        data = resp.json() if resp.content else {}
        status = (data.get("status") or "").strip()
        return status, None
    except Exception as exc:
        logger.warning("Failed to fetch Node status for session %s: %s", session_id, exc)
        return None, str(exc)

def _fetch_live_sessions():
    """Return (sessions, error) from the Node service."""
    base = _get_node_base_url()
    try:
        resp = requests.get(f"{base}/sessions", timeout=10)
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        data = resp.json() if resp.content else {}
        sessions = data.get("sessions") or []
        if not isinstance(sessions, list):
            return [], "invalid_sessions_payload"
        return sessions, None
    except Exception as exc:
        logger.warning("Failed to fetch Node sessions: %s", exc)
        return [], str(exc)

def _pick_connected_device():
    device = frappe.get_list(
        "WhatsApp Device",
        filters={"status": "Connected"},
        fields=["name", "number"],
        limit=1,
    )
    if device:
        return device[0]

    sessions, _ = _fetch_live_sessions()
    for session in sessions:
        if (session.get("status") or "").lower() != "connected":
            continue
        session_id = session.get("session")
        device_name = resolve_device_name(session_id)
        if not device_name:
            continue
        number = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_id
        mark_device_active(device_name, status="Connected")
        return {"name": device_name, "number": number}

    return None

def _normalize_status(value):
    return (value or "").strip().lower()

def _session_not_ready_message(session_label, node_status):
    status = _normalize_status(node_status)
    if status in {"waiting for scan", "waiting"}:
        return f"Session '{session_label}' is waiting for a QR scan. Open the WhatsApp Device record and scan the code again."
    if status in {"connecting"}:
        return f"Session '{session_label}' is still connecting. Please wait a few seconds and retry."
    if status in {"disconnected"}:
        return f"Session '{session_label}' is disconnected. Reset the session and scan the QR again."
    if node_status:
        return f"Session '{session_label}' is reported as '{node_status}'. Please re-link the device."
    return f"Session '{session_label}' is not connected. Please scan the QR code again."

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
            session_to_use = frappe.db.get_value("WhatsApp Device", device_name, "number") or session_id
            device_status = frappe.db.get_value("WhatsApp Device", device_name, "status")
            if device_status != "Connected":
                node_status, status_error = _fetch_node_session_status(session_to_use)
                normalized = _normalize_status(node_status)
                if normalized == "connected":
                    mark_device_active(device_name, status="Connected")
                elif node_status:
                    raise Exception(_session_not_ready_message(session_id, node_status))
                elif status_error:
                    logger.warning(
                        "Unable to verify Node status for session %s (%s). Proceeding with send attempt.",
                        session_id,
                        status_error,
                    )
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
        status, error = _fetch_node_session_status(session_id)
        if status:
            return {"status": status}
        return {"status": "Error", "message": error or "Unknown status"}
    except Exception as e:
        frappe.log_error(f"Status check error (Node): {str(e)}", "WhatsApp Status Check")
        return {"status": "Error", "message": str(e)}
