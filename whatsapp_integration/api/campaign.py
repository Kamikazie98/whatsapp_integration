import frappe
from frappe.utils.background_jobs import enqueue
from frappe.utils import now_datetime, getdate, nowdate, add_days
from datetime import datetime, timedelta

MAX_RETRY_ATTEMPTS = 3

@frappe.whitelist()
def run_campaign(campaign_id):
    """Trigger campaign via background job"""
    campaign = frappe.get_doc("WhatsApp Campaign", campaign_id)

    if campaign.schedule_type == "Send Now":
        enqueue(
            "whatsapp_integration.api.campaign.process_campaign",
            queue="default",
            campaign_id=campaign_id
        )
        return {"message": "Campaign started in background."}

    elif campaign.schedule_type == "Schedule for Later":
        if not campaign.schedule_time:
            frappe.throw("Please set Schedule Time")
        
        campaign.status = "Scheduled"
        campaign.save(ignore_permissions=True)
        return {"message": f"Campaign scheduled for {campaign.schedule_time}"}


def process_campaign(campaign_id):
    """Actual worker job: Send messages"""
    campaign = frappe.get_doc("WhatsApp Campaign", campaign_id)
    if campaign.status not in ["Draft", "Failed", "Scheduled"]:
        return
    
    total = len(campaign.recipients)
    campaign.total_recipients = total
    campaign.sent_count = 0
    campaign.failed_count = 0
    campaign.progress = 0
    campaign.status = "Running"
    campaign.save(ignore_permissions=True)
    frappe.db.commit()

    for idx, row in enumerate(campaign.recipients):
        try:
            from whatsapp_integration.api.whatsapp import send_whatsapp_message
            send_whatsapp_message(row.number, campaign.message_template)
            row.status = "Sent"
            row.sent_time = frappe.utils.now()
            campaign.sent_count += 1
        except Exception as e:
            row.status = "Failed"
            row.error_message = str(e)
            campaign.failed_count += 1
        
        campaign.progress = int(((idx + 1) / total) * 100)
        campaign.save(ignore_permissions=True)
        frappe.db.commit()
    
    campaign.status = "Completed"
    campaign.save(ignore_permissions=True)


@frappe.whitelist()
def load_recipients(campaign_id, source, filter_by=None):
    """Auto-load recipients based on source"""
    campaign = frappe.get_doc("WhatsApp Campaign", campaign_id)
    
    recipients = []
    if source == "Lead":
        filters = {"status": filter_by} if filter_by else {}
        leads = frappe.get_all("Lead", filters=filters, fields=["name", "phone"])
        for l in leads:
            if l.phone:
                recipients.append({"party_type": "Lead", "party_name": l.name, "number": l.phone})
    
    elif source == "Customer":
        filters = {"customer_group": filter_by} if filter_by else {}
        customers = frappe.get_all("Customer", filters=filters, fields=["name", "mobile_no"])
        for c in customers:
            if c.mobile_no:
                recipients.append({"party_type": "Customer", "party_name": c.name, "number": c.mobile_no})
    
    elif source == "Contact":
        contacts = frappe.get_all("Contact", fields=["name", "mobile_no"])
        for c in contacts:
            if c.mobile_no:
                recipients.append({"party_type": "Contact", "party_name": c.name, "number": c.mobile_no})
    
    elif source == "Territory":
        customers = frappe.get_all("Customer", {"territory": filter_by}, fields=["name", "mobile_no"])
        for c in customers:
            if c.mobile_no:
                recipients.append({"party_type": "Customer", "party_name": c.name, "number": c.mobile_no})
    
    # Clear old recipients and add new ones
    campaign.set("recipients", [])
    for r in recipients:
        campaign.append("recipients", r)
    
    campaign.save()
    return {"loaded": len(recipients)}


def check_scheduled_campaigns():
    """Find and run campaigns scheduled for now"""
    now = now_datetime()

    campaigns = frappe.get_all(
        "WhatsApp Campaign",
        filters={"status": "Scheduled"},
        fields=["name", "schedule_time"]
    )

    for c in campaigns:
        if c.schedule_time and c.schedule_time <= now:
            enqueue(
                "whatsapp_integration.api.campaign.process_campaign",
                queue="default",
                campaign_id=c.name
            )
            doc = frappe.get_doc("WhatsApp Campaign", c.name)
            doc.status = "Queued"
            doc.save(ignore_permissions=True)


def check_recurring_campaigns():
    """Check and enqueue recurring campaigns"""
    now = now_datetime()
    today = getdate(now)

    campaigns = frappe.get_all(
        "WhatsApp Campaign",
        filters={"is_recurring": 1},
        fields=["name", "recurrence_type", "day_of_week", "day_of_month", "end_date"]
    )

    for c in campaigns:
        # Skip if expired
        if c.end_date and getdate(c.end_date) < today:
            continue

        run_this = False

        if c.recurrence_type == "Daily":
            run_this = True

        elif c.recurrence_type == "Weekly":
            if c.day_of_week and today.strftime("%A") == c.day_of_week:
                run_this = True

        elif c.recurrence_type == "Monthly":
            if c.day_of_month and today.day == int(c.day_of_month):
                run_this = True

        if run_this:
            enqueue(
                "whatsapp_integration.api.campaign.process_campaign",
                queue="default",
                campaign_id=c.name
            )

            # Log last run
            doc = frappe.get_doc("WhatsApp Campaign", c.name)
            doc.last_run = now
            doc.status = "Queued"
            doc.save(ignore_permissions=True)


@frappe.whitelist()
def auto_retry_failed():
    """Automatically retry failed WhatsApp messages"""
    recipients = frappe.get_all(
        "WhatsApp Campaign Recipient",
        filters={"status": "Failed"},
        fields=["name", "number", "message", "parent", "retry_count"]
    )

    retried = 0
    for rec in recipients:
        if rec.retry_count and rec.retry_count >= MAX_RETRY_ATTEMPTS:
            # Mark as permanently failed
            frappe.db.set_value("WhatsApp Campaign Recipient", rec.name, "status", "Permanently Failed")
            continue

        try:
            from whatsapp_integration.api.whatsapp import send_whatsapp_message
            send_whatsapp_message(rec.number, rec.message)
            frappe.db.set_value("WhatsApp Campaign Recipient", rec.name, {
                "status": "Retrying",
                "retry_count": (rec.retry_count or 0) + 1,
                "last_retry_time": frappe.utils.now()
            })
            retried += 1
        except Exception as e:
            frappe.log_error(f"Retry failed for {rec.name}: {str(e)}", "WhatsApp Retry Error")

    return f"{retried} messages re-queued automatically"


@frappe.whitelist()
def bulk_retry(date=None, campaign=None):
    """Retry all failed recipients for a given date or campaign"""
    conditions = ["status='Failed'"]
    params = []

    if date:
        conditions.append("DATE(sent_time)=%s")
        params.append(date)
    if campaign:
        conditions.append("parent=%s")
        params.append(campaign)

    query = f"""
        SELECT name, number, message, parent
        FROM `tabWhatsApp Campaign Recipient`
        WHERE {" AND ".join(conditions)}
    """
    failed_recipients = frappe.db.sql(query, tuple(params), as_dict=True)

    from whatsapp_integration.api.whatsapp import send_whatsapp_message
    retried = 0

    for rec in failed_recipients:
        send_whatsapp_message(rec.number, rec.message)
        frappe.db.set_value("WhatsApp Campaign Recipient", rec.name, "status", "Retrying")
        retried += 1

    return f"{retried} failed messages re-queued for sending"


@frappe.whitelist()
def retry_recipient(recipient_id):
    """Retry sending a failed WhatsApp message"""
    rec = frappe.get_doc("WhatsApp Campaign Recipient", recipient_id)
    if rec.status != "Failed":
        frappe.throw("Only failed messages can be retried.")

    from whatsapp_integration.api.whatsapp import send_whatsapp_message
    send_whatsapp_message(rec.number, rec.message)

    rec.status = "Retrying"
    rec.save(ignore_permissions=True)

    return "Message re-queued for sending"


@frappe.whitelist()
def update_campaign_stats(doc=None, method=None):
    """Update delivery analytics for a given campaign"""
    campaign_id = doc.name if doc else None
    if not campaign_id:
        return
    
    statuses = frappe.get_all(
        "WhatsApp Campaign Recipient",
        filters={"parent": campaign_id},
        fields=["status", "retry_count"]
    )

    total = len(statuses)
    delivered = sum(1 for r in statuses if r.status == "Sent")
    failed = sum(1 for r in statuses if r.status == "Failed")
    permanent = sum(1 for r in statuses if r.status == "Permanently Failed")
    retries = sum(r.retry_count or 0 for r in statuses)

    success_rate = (delivered / total * 100) if total > 0 else 0

    frappe.db.set_value("WhatsApp Campaign", campaign_id, {
        "total_recipients": total,
        "sent_count": delivered,
        "failed_count": failed,
        "permanent_failures": permanent,
        "retry_count": retries,
        "success_rate": round(success_rate, 2)
    })

    return {
        "total": total,
        "delivered": delivered,
        "failed": failed,
        "permanent": permanent,
        "retries": retries,
        "success_rate": success_rate
    }
