frappe.ui.form.on("WhatsApp Campaign", {
    refresh: function(frm) {
        // Progress bar for running campaigns
        if (frm.doc.status === "Running") {
            frm.dashboard.show_progress(
                __("Sending WhatsApp Messages"),
                frm.doc.progress
            );
            
            // Auto-refresh progress every 5 seconds
            setTimeout(() => {
                frm.reload_doc();
            }, 5000);
        } else if (frm.doc.status === "Completed") {
            frm.dashboard.show_progress(
                __("Campaign Completed"),
                100
            );
        }

        // Show recurrence info
        if (frm.doc.is_recurring) {
            let msg = `🔁 Recurring: ${frm.doc.recurrence_type}`;
            if (frm.doc.recurrence_type === "Weekly")
                msg += ` (Every ${frm.doc.day_of_week})`;
            if (frm.doc.recurrence_type === "Monthly")
                msg += ` (Day ${frm.doc.day_of_month})`;
            frm.dashboard.add_comment(__(msg));
        }

        if (frm.doc.status === "Scheduled") {
            frm.dashboard.add_comment(__('⏰ Scheduled for ' + frm.doc.schedule_time));
        }

        // Load Recipients button
        frm.add_custom_button(__('📥 Load Recipients'), function() {
            frappe.prompt([
                {
                    label: 'Source',
                    fieldname: 'source',
                    fieldtype: 'Select',
                    options: ['Lead','Customer','Contact','Territory'],
                    reqd: 1
                },
                {
                    label: 'Filter By',
                    fieldname: 'filter_by',
                    fieldtype: 'Data',
                    description: 'e.g. Lead Status = Interested, Customer Group = Retail, Territory = India'
                }
            ], function(values){
                frappe.call({
                    method: "whatsapp_integration.api.campaign.load_recipients",
                    args: {
                        campaign_id: frm.doc.name,
                        source: values.source,
                        filter_by: values.filter_by
                    },
                    callback: function(r) {
                        frappe.show_alert({
                            message: `✅ ${r.message.loaded} recipients loaded.`,
                            indicator: 'green'
                        });
                        frm.reload_doc();
                    }
                });
            }, __("Load Recipients"), __("Load"));
        });

        // Run Campaign button
        if (!frm.doc.__islocal && frm.doc.status !== "Running") {
            frm.add_custom_button(__('▶️ Run Campaign'), function() {
                frappe.call({
                    method: "whatsapp_integration.api.campaign.run_campaign",
                    args: { campaign_id: frm.doc.name },
                    callback: function(r) {
                        frappe.show_alert({
                            message: r.message.message,
                            indicator: 'green'
                        });
                        frm.reload_doc();
                    }
                });
            });
        }

        // Update Analytics button
        if (!frm.doc.__islocal) {
            frm.add_custom_button("📊 Update Analytics", function() {
                frappe.call({
                    method: "whatsapp_integration.api.campaign.update_campaign_stats",
                    args: { campaign_id: frm.doc.name },
                    callback: function(r) {
                        if (r.message) {
                            frappe.msgprint("📊 Analytics Updated");
                            frm.reload_doc();
                        }
                    }
                });
            }, "Actions");
        }
    }
});
