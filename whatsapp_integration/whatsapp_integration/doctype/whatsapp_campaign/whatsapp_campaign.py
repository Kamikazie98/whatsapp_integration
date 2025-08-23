import frappe
from frappe.model.document import Document

class WhatsAppCampaign(Document):
    def before_save(self):
        if not self.status:
            self.status = "Draft"
