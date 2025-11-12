import frappe

def update_whatsapp_settings():
    """Update WhatsApp Settings with correct NodeJS URL"""
    try:
        settings = frappe.get_doc("WhatsApp Settings")
        settings.mode = "Unofficial"
        settings.nodejs_url = "http://localhost:8001"
        settings.save(ignore_permissions=True)
        print("✅ WhatsApp Settings updated successfully!")
        print(f"Mode: {settings.mode}")
        print(f"NodeJS URL: {settings.nodejs_url}")
    except Exception as e:
        print(f"❌ Error updating settings: {e}")

update_whatsapp_settings()
