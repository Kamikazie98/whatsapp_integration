frappe.ui.form.on('WhatsApp Dashboard', {
    refresh: function(frm) {
        // KPIs
        frm.dashboard.add_section('<h3>WhatsApp Overview</h3>');
        frm.dashboard.add_indicator(__('Devices: ') + frm.doc.total_devices, 
            frm.doc.total_devices > 0 ? 'green' : 'red');

        frm.dashboard.add_indicator(__('Messages Sent: ') + frm.doc.messages_sent, 'blue');
        frm.dashboard.add_indicator(__('Messages Received: ') + frm.doc.messages_received, 'orange');

        frm.dashboard.add_indicator(__('Subscription: ') + frm.doc.subscription_status, 
            frm.doc.subscription_status === 'Active' ? 'green' : 'red');

        frm.dashboard.add_comment(__('Last Sync: ') + (frm.doc.last_sync || 'Never'));

        // Quick Actions Panel
        frm.dashboard.add_section('<h3>Quick Actions</h3>');

        frm.add_custom_button(__('➕ Add Device'), function() {
            frappe.call({
                method: "whatsapp_integration.api.dashboard_actions.add_device",
                args: { session_name: "default" },
                callback: function(r) {
                    if(r.message.qr) {
                        frappe.msgprint(__('Scan this QR in WhatsApp App'));
                        frappe.show_alert({ message: "Device Added, Scan QR", indicator: 'green' });
                    }
                }
            });
        }, __("Quick Actions"));

        frm.add_custom_button(__('✉️ Send Test Message'), function() {
            frappe.prompt(
                [{fieldtype:'Data', label:'WhatsApp Number (with country code)', fieldname:'number', reqd:1}],
                function(values){
                    frappe.call({
                        method: "whatsapp_integration.api.dashboard_actions.send_test_message",
                        args: { number: values.number },
                        callback: function(r) {
                            frappe.show_alert({ message: "Test message sent", indicator: 'green' });
                        }
                    });
                },
                __("Send Test Message"),
                __("Send")
            );
        }, __("Quick Actions"));

        frm.add_custom_button(__('🔄 Sync Now'), function() {
            frappe.call({
                method: "whatsapp_integration.api.dashboard_actions.sync_now",
                callback: function(r) {
                    frappe.show_alert({ message: "Dashboard synced", indicator: 'blue' });
                    frm.reload_doc();
                }
            });
        }, __("Quick Actions"));

        // Campaigns
        frm.add_custom_button(__('📢 New Campaign'), function() {
            frappe.new_doc('WhatsApp Campaign');
        }, __("Campaigns"));

        frm.add_custom_button(__('▶️ Run Campaign'), function() {
            frappe.prompt(
                [{fieldtype:'Link', label:'Campaign', options:'WhatsApp Campaign', fieldname:'campaign', reqd:1}],
                function(values){
                    frappe.call({
                        method: "whatsapp_integration.api.campaign.run_campaign",
                        args: { campaign_id: values.campaign },
                        callback: function(r) {
                            frappe.show_alert({ message: r.message.message, indicator: 'green' });
                        }
                    });
                },
                __("Run WhatsApp Campaign"),
                __("Run")
            );
        }, __("Campaigns"));

        // Charts
        if(frm.doc.messages_trend) {
            let data = JSON.parse(frm.doc.messages_trend);
            frm.dashboard.add_section('<div id="messages-trend" style="height:300px;"></div>');
            new frappe.Chart("#messages-trend", {
                title: "Messages Trend (Last 7 Days)",
                data: data,
                type: 'line',
                height: 300,
                colors: ['#36a2eb', '#ff6384']
            });
        }

        if(frm.doc.device_usage) {
            let data = JSON.parse(frm.doc.device_usage);
            frm.dashboard.add_section('<div id="device-usage" style="height:250px;"></div>');
            new frappe.Chart("#device-usage", {
                title: "Device Usage",
                data: data,
                type: 'pie',
                height: 250,
                colors: ['#4caf50', '#f44336']
            });
        }
    }
});
