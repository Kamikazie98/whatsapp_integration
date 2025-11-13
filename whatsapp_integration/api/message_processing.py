# -*- coding: utf-8 -*-
import frappe

def process_incoming_message(payload):
    """
    Process incoming WhatsApp messages from the queue.
    This function runs in the background.
    """
    try:
        # Example: Log the message to a custom DocType
        log = frappe.new_doc("WhatsApp Message Log")
        log.sender = payload.get("from")
        log.recipient = payload.get("to")
        log.message_content = payload.get("body")
        log.sent_by_us = 0
        log.status = "Received"
        log.insert(ignore_permissions=True)
        frappe.db.commit()

        frappe.logger("whatsapp").info(f"Successfully processed message from {payload.get('from')}")

    except Exception as e:
        frappe.logger("whatsapp").error(
            f"Failed to process incoming message: {e}",
            context={"payload": payload},
        )
        raise

def process_status_update(payload):
    """
    Process WhatsApp status updates from the queue.
    This function runs in the background.
    """
    try:
        device_name = payload.get("device")
        status = payload.get("status")

        if not device_name or not status:
            raise ValueError("Missing device or status in payload")

        frappe.db.set_value("WhatsApp Device", device_name, "status", status)
        frappe.db.commit()

        frappe.logger("whatsapp").info(f"Successfully updated status for device {device_name} to {status}")

    except Exception as e:
        frappe.logger("whatsapp").error(
            f"Failed to process status update: {e}",
            context={"payload": payload},
        )
        raise

__all__ = ["process_incoming_message", "process_status_update"]
