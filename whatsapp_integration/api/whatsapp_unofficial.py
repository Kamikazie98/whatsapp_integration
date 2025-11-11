import re
import frappe


def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _pick_connected_session():
    """Pick a usable session id from WhatsApp Device or active QR sessions."""
    # Prefer a DocType explicitly marked Connected
    connected = frappe.db.get_value("WhatsApp Device", {"status": "Connected"}, "name")
    if connected:
        return connected

    # Otherwise, look through recent devices and see if QR service says connected
    try:
        from whatsapp_integration.api.whatsapp_real_qr import check_qr_status

        devices = frappe.get_all(
            "WhatsApp Device", fields=["name", "status"], order_by="modified desc", limit=5
        )
        for d in devices:
            st = check_qr_status(d.name) or {}
            if st.get("status") in {"connected"}:
                return d.name
    except Exception:
        pass

    # As a last resort return the most recent device (may still fail later)
    any_dev = frappe.db.get_value("WhatsApp Device", {}, "name")
    return any_dev


def send_unofficial(number, message):
    """Send message using an active Selenium (WhatsApp Web) session.

    - Select a connected device from DocType `WhatsApp Device` as session_id
    - Use persistent driver from whatsapp_real_qr if available
    - Fallback to basic whatsapp_python sender
    """
    try:
        session_id = _pick_connected_session()
        if not session_id:
            raise Exception("No connected WhatsApp device. Please scan QR and connect a device first.")

        dest = _digits_only(number)
        if not dest:
            raise Exception("Invalid destination number")

        # Try persistent driver first
        try:
            from whatsapp_integration.api.whatsapp_real_qr import send_message_persistent

            result = send_message_persistent(session_id, dest, message)
        except Exception:
            # Fallback to simple python sender
            from whatsapp_integration.api.whatsapp_python import send_message

            result = send_message(session_id, dest, message)

        if result.get("success"):
            return result
        else:
            raise Exception(result.get("error", "Failed to send message"))

    except Exception as e:
        frappe.log_error(f"Unofficial send error: {str(e)}", "WhatsApp Unofficial Send")
        raise Exception(f"Failed to send message: {str(e)}")

@frappe.whitelist()
def check_device_status(session_id):
    """Check if WhatsApp device is connected using Python service"""
    try:
        # Prefer persistent QR service if available
        try:
            from whatsapp_integration.api.whatsapp_real_qr import check_qr_status

            qr_status = check_qr_status(session_id)
            status = qr_status.get("status")
        except Exception:
            from whatsapp_integration.api.whatsapp_python import check_session_status

            result = check_session_status(session_id)
            status = result.get("status")

        return {"status": status or "Unknown"}
        
    except Exception as e:
        frappe.log_error(f"Status check error: {str(e)}", "WhatsApp Status Check")
        return {"status": "Error", "message": str(e)}
