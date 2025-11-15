import requests
import frappe
from frappe import as_json
from frappe.model.document import Document
from frappe.utils import nowdate, add_days

from whatsapp_integration.api.whatsapp_unofficial import _get_node_base_url


class WhatsAppDashboard(Document):
    def _fetch_node_sessions(self):
        """Return (sessions, error_message) from the Node.js service."""
        base_url = _get_node_base_url()
        try:
            resp = requests.get(f"{base_url}/sessions", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            return data.get("sessions", []), None
        except Exception as exc:
            frappe.log_error(f"Failed to fetch Node sessions: {exc}", "WhatsApp Dashboard")
            return [], str(exc)

    @staticmethod
    def _summarize_sessions(sessions):
        summary = {"total": 0, "connected": 0, "waiting": 0, "disconnected": 0}
        for session in sessions:
            summary["total"] += 1
            status = (session.get("status") or "").lower()
            if status == "connected":
                summary["connected"] += 1
            elif status in {"waiting for scan", "waiting"}:
                summary["waiting"] += 1
            else:
                summary["disconnected"] += 1
        return summary

    def get_context(self, context):
        sessions, node_error = self._fetch_node_sessions()
        session_summary = self._summarize_sessions(sessions)

        context.total_devices = session_summary["total"]
        context.connected_devices = session_summary["connected"]
        context.waiting_devices = session_summary["waiting"]
        context.disconnected_devices = session_summary["disconnected"]
        context.node_sync_error = node_error
        context.node_sessions = as_json(sessions)

        context.messages_sent = frappe.db.count("WhatsApp Message Log", {"direction": "Out"})
        context.messages_received = frappe.db.count("WhatsApp Message Log", {"direction": "In"})
        context.subscription_status = (
            "Active" if frappe.db.get_single_value("WhatsApp Settings", "access_token") else "Expired"
        )
        context.last_sync = frappe.db.get_value("WhatsApp Device", {"status": "Connected"}, "modified")

        # Messages trend for last 7 days
        labels, sent_data, recv_data = [], [], []
        for i in range(6, -1, -1):
            day = add_days(nowdate(), -i)
            sent = frappe.db.count(
                "WhatsApp Message Log",
                {"direction": "Out", "creation": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]]},
            )
            recv = frappe.db.count(
                "WhatsApp Message Log",
                {"direction": "In", "creation": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]]},
            )
            labels.append(day)
            sent_data.append(sent)
            recv_data.append(recv)

        context.messages_trend = as_json(
            {
                "labels": labels,
                "datasets": [
                    {"name": "Sent", "values": sent_data},
                    {"name": "Received", "values": recv_data},
                ],
            }
        )

        # Device usage breakdown based on live Node sessions
        context.device_usage = as_json(
            {
                "labels": ["Connected", "Waiting", "Disconnected"],
                "datasets": [
                    {
                        "name": "Sessions",
                        "values": [
                            session_summary["connected"],
                            session_summary["waiting"],
                            session_summary["disconnected"],
                        ],
                    }
                ],
            }
        )

        return context
