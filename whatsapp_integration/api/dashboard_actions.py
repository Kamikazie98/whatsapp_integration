import frappe
import requests
from whatsapp_integration.api.whatsapp_unofficial import _get_node_base_url

@frappe.whitelist()
def add_device(session_name="default"):
    """Trigger QR session creation in Unofficial mode"""
    settings = frappe.get_doc("WhatsApp Settings")
    if settings.mode != "Unofficial":
        return {"error": "Add Device works only in Unofficial mode"}
    
    sanitized = (session_name or "default").strip() or "default"
    base_url = _get_node_base_url()
    res = requests.get(f"{base_url}/qr/{sanitized}", timeout=20)
    res.raise_for_status()
    payload = res.json()
    qr_value = payload.get("qr")
    status = "QR Generated" if qr_value else "Disconnected"

    if frappe.db.exists("WhatsApp Device", sanitized):
        device = frappe.get_doc("WhatsApp Device", sanitized)
        device.qr_code = qr_value
        device.status = status
        device.save(ignore_permissions=True)
    else:
        device = frappe.get_doc({
            "doctype": "WhatsApp Device",
            "number": sanitized,
            "qr_code": qr_value,
            "status": status
        })
        device.insert(ignore_permissions=True)

    return {"message": "Device added, scan QR in WhatsApp", "qr": qr_value, "session": sanitized}

@frappe.whitelist()
def send_test_message(number, session=None):
    """Send test ping"""
    from whatsapp_integration.api.whatsapp import send_whatsapp_message
    return send_whatsapp_message(number, "✅ WhatsApp Integration Test from ERPNext", session=session)

@frappe.whitelist()
def sync_now():
    """Force dashboard refresh"""
    frappe.db.commit()
    return {"message": "Sync complete"}

@frappe.whitelist()
def get_dashboard_data():
    """Return analytics for WhatsApp campaigns"""
    total_campaigns = frappe.db.count("WhatsApp Campaign")
    total_sent = frappe.db.count("WhatsApp Campaign Recipient", {"status": "Sent"})
    total_failed = frappe.db.count("WhatsApp Campaign Recipient", {"status": "Failed"})
    
    success_rate = 0
    if total_sent + total_failed > 0:
        success_rate = round((total_sent / (total_sent + total_failed)) * 100, 2)

    # Last campaign
    last = frappe.db.get_value("WhatsApp Campaign", {}, "name", order_by="creation desc")

    # Trend data (last 7 days)
    daily_stats = frappe.db.sql("""
        SELECT DATE(sent_time) as date,
               COUNT(*) as sent_count,
               SUM(case when status='Failed' then 1 else 0 end) as failed_count
        FROM `tabWhatsApp Campaign Recipient`
        WHERE sent_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        GROUP BY DATE(sent_time)
        ORDER BY date ASC
    """, as_dict=True)

    return {
        "total_campaigns": total_campaigns,
        "total_sent": total_sent,
        "total_failed": total_failed,
        "success_rate": success_rate,
        "last_campaign": last,
        "daily_stats": daily_stats
    }

@frappe.whitelist()
def get_drilldown(date=None, status=None):
    """Return list of recipients for a given date & status"""
    recipients = frappe.db.sql("""
        SELECT wr.name, wr.number, wr.status, wr.sent_time, wr.message, wc.name as campaign
        FROM `tabWhatsApp Campaign Recipient` wr
        LEFT JOIN `tabWhatsApp Campaign` wc ON wc.name = wr.parent
        WHERE DATE(wr.sent_time)=%s AND wr.status=%s
        ORDER BY wr.sent_time DESC
    """, (date, status), as_dict=True)

    return recipients

@frappe.whitelist()
def get_delivery_stats():
    """Get global WhatsApp delivery stats"""
    statuses = ["Sent", "Failed", "Retrying", "Permanently Failed", "Pending"]
    data = {}

    for s in statuses:
        count = frappe.db.count("WhatsApp Campaign Recipient", {"status": s})
        data[s] = count

    return data
