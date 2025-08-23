import frappe
from frappe.utils import getdate, today, add_days, nowdate

def execute(filters=None):
    if not filters:
        filters = {}

    from_date = getdate(filters.get("from_date") or add_days(today(), -30))
    to_date = getdate(filters.get("to_date") or today())

    columns = get_columns()
    data = get_data(from_date, to_date)
    chart = get_chart(data)

    # Professional report summary for PDF
    report_summary = [
        {
            "label": "Report",
            "value": "WhatsApp Campaign Leaderboard",
            "indicator": "blue"
        },
        {
            "label": "Date Range",
            "value": f"{from_date} to {to_date}",
            "indicator": "green"
        },
        {
            "label": "Total Campaigns",
            "value": len(data),
            "indicator": "orange"
        }
    ]

    return columns, data, None, chart, report_summary


def get_columns():
    return [
        {"label": "Campaign", "fieldname": "campaign", "fieldtype": "Link", "options": "WhatsApp Campaign", "width": 200},
        {"label": "Total", "fieldname": "total_recipients", "fieldtype": "Int", "width": 80},
        {"label": "Delivered", "fieldname": "delivered", "fieldtype": "Int", "width": 80},
        {"label": "Failed", "fieldname": "failed", "fieldtype": "Int", "width": 80},
        {"label": "Permanent Failures", "fieldname": "permanent", "fieldtype": "Int", "width": 120},
        {"label": "Retries Used", "fieldname": "retries", "fieldtype": "Int", "width": 100},
        {"label": "Success Rate %", "fieldname": "success_rate", "fieldtype": "Percent", "width": 120},
    ]


def get_data(from_date, to_date):
    return frappe.db.sql("""
        SELECT
            name as campaign,
            total_recipients,
            sent_count as delivered,
            failed_count as failed,
            permanent_failures as permanent,
            retry_count as retries,
            success_rate
        FROM `tabWhatsApp Campaign`
        WHERE creation BETWEEN %s AND %s
        ORDER BY success_rate DESC
        LIMIT 10
    """, (from_date, to_date), as_dict=True)


def get_chart(data):
    if not data:
        return None

    labels = [d["campaign"] for d in data]
    success_rates = [d["success_rate"] for d in data]
    failures = [d["failed"] + d["permanent"] for d in data]

    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": "Success Rate %", "values": success_rates},
                {"name": "Failures", "values": failures}
            ]
        },
        "type": "bar",
        "colors": ["#21ba45", "#db2828"],
        "barOptions": {"stacked": False}
    }
