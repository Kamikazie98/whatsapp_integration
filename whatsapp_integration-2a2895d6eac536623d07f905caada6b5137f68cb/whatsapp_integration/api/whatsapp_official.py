import requests
import frappe

def send_official(number, message):
    """Send message via Official WhatsApp Cloud API"""
    settings = frappe.get_doc("WhatsApp Settings")
    url = f"https://graph.facebook.com/v19.0/{settings.phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {settings.access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": number,
        "type": "text",
        "text": {"body": message}
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise Exception(f"WhatsApp API error: {response.text}")
    return response.json()
