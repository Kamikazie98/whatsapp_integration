import frappe

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
                        frappe.get_doc({
                            "doctype": "WhatsApp Message Log",
                            "number": msg["from"],
                            "message": msg["text"]["body"],
                            "direction": "In",
                            "status": "Received"
                        }).insert(ignore_permissions=True)

        else:
            # Unofficial webhook payload
            frappe.get_doc({
                "doctype": "WhatsApp Message Log",
                "number": data.get("from"),
                "message": data.get("text"),
                "direction": "In",
                "status": "Received"
            }).insert(ignore_permissions=True)

        return {"status": "ok"}
