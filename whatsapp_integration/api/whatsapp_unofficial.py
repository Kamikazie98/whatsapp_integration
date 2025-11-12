import os
import requests
import frappe

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
    return "http://localhost:3001"

def send_unofficial(number, message):
    """Send message via Node.js WhatsApp service"""
    try:
        base = _get_node_base_url()
        payload = {"session": number, "to": number, "message": message}
        resp = requests.post(f"{base}/sendMessage", json=payload, timeout=20)
        if resp.status_code != 200:
            raise Exception(f"Node send error: HTTP {resp.status_code} {resp.text}")
        data = resp.json()
        if not data.get("success"):
            raise Exception(data.get("error") or "Unknown send error")
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
