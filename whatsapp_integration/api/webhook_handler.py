# -*- coding: utf-8 -*-
import frappe
from frappe.utils.data import sbool

@frappe.whitelist(allow_guest=True)
def handle_webhook():
    """Unified webhook entrypoint."""
    try:
        # Get data from request
        request_data = frappe.request.json
        if not isinstance(request_data, dict):
            raise ValueError("Invalid JSON data")

        # Get event type from request data
        event_type = request_data.get("event")
        if not event_type:
            raise ValueError("Missing 'event' type in request data")

        # Log the raw event for debugging
        frappe.logger("whatsapp").debug({"title": "Received webhook event", "data": request_data})

        # Route to the appropriate handler
        handler = get_handler_for_event(event_type)
        if not handler:
            raise NotImplementedError(f"No handler for event type '{event_type}'")

        handler(request_data)
        return {"status": "ok"}

    except Exception as e:
        frappe.logger("whatsapp").error({"title": "Webhook handling failed", "exception": str(e)})
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}

def get_handler_for_event(event_type):
    """Return the handler function for a given event type."""
    event_handlers = {
        "message": handle_incoming_message,
        "status": handle_status_update,
    }
    return event_handlers.get(event_type)

def handle_incoming_message(payload):
    """Enqueue incoming WhatsApp messages for background processing."""
    frappe.enqueue(
        "whatsapp_integration.api.message_processing.process_incoming_message",
        queue="short",
        timeout=300,
        payload=payload,
    )

def handle_status_update(payload):
    """Enqueue WhatsApp status updates for background processing."""
    frappe.enqueue(
        "whatsapp_integration.api.message_processing.process_status_update",
        queue="short",
        timeout=300,
        payload=payload,
    )
