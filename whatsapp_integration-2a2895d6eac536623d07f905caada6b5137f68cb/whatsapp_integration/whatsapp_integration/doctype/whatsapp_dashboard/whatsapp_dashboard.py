import frappe
from frappe.model.document import Document
from frappe.utils import nowdate, add_days

class WhatsAppDashboard(Document):
    def get_context(self, context):
        context.total_devices = frappe.db.count("WhatsApp Device", {"status": "Connected"})
        context.messages_sent = frappe.db.count("WhatsApp Message Log", {"direction": "Out"})
        context.messages_received = frappe.db.count("WhatsApp Message Log", {"direction": "In"})
        context.subscription_status = "Active" if frappe.db.get_single_value("WhatsApp Settings", "access_token") else "Expired"
        context.last_sync = frappe.db.get_value("WhatsApp Device", {"status": "Connected"}, "modified")

        # Messages trend for last 7 days
        labels, sent_data, recv_data = [], [], []
        for i in range(6, -1, -1):
            day = add_days(nowdate(), -i)
            sent = frappe.db.count("WhatsApp Message Log", {"direction": "Out", "creation": ["between", [day + " 00:00:00", day + " 23:59:59"]]})
            recv = frappe.db.count("WhatsApp Message Log", {"direction": "In", "creation": ["between", [day + " 00:00:00", day + " 23:59:59"]]})
            labels.append(day)
            sent_data.append(sent)
            recv_data.append(recv)

        context.messages_trend = {
            "labels": labels,
            "datasets": [
                {"name": "Sent", "values": sent_data},
                {"name": "Received", "values": recv_data}
            ]
        }

        # Device usage breakdown
        connected = frappe.db.count("WhatsApp Device", {"status": "Connected"})
        disconnected = frappe.db.count("WhatsApp Device", {"status": "Disconnected"})
        context.device_usage = {
            "labels": ["Connected", "Disconnected"],
            "datasets": [{"name": "Devices", "values": [connected, disconnected]}]
        }

        return context
