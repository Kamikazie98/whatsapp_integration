frappe.ui.form.on('WhatsApp Dashboard', {
    refresh: function(frm) {
        // KPIs
        frm.dashboard.add_section('<h3>WhatsApp Overview</h3>');
        frm.dashboard.add_indicator(__('Devices: ') + (frm.doc.total_devices || 0), 
            (frm.doc.connected_devices || 0) > 0 ? 'green' : 'red');
        frm.dashboard.add_indicator(__('Connected: ') + (frm.doc.connected_devices || 0), 'green');
        frm.dashboard.add_indicator(__('Waiting: ') + (frm.doc.waiting_devices || 0), 'orange');
        frm.dashboard.add_indicator(__('Disconnected: ') + (frm.doc.disconnected_devices || 0), 'red');

        frm.dashboard.add_indicator(__('Messages Sent: ') + (frm.doc.messages_sent || 0), 'blue');
        frm.dashboard.add_indicator(__('Messages Received: ') + (frm.doc.messages_received || 0), 'orange');

        frm.dashboard.add_indicator(__('Subscription: ') + frm.doc.subscription_status, 
            frm.doc.subscription_status === 'Active' ? 'green' : 'red');

        frm.dashboard.add_comment(__('Last Sync: ') + (frm.doc.last_sync || 'Never'));
        if (frm.doc.node_sync_error) {
            frm.dashboard.add_comment(__('Node sync error: {0}', [frm.doc.node_sync_error]));
        }

        // Quick Actions Panel
        frm.dashboard.add_section('<h3>Quick Actions</h3>');

        frm.add_custom_button(__('Add Device'), function() {
            frappe.prompt(
                [{
                    fieldtype:'Data',
                    label: __('Session Name'),
                    fieldname:'session_name',
                    reqd:1,
                    default: 'default',
                    description: __('Letters, numbers, dash or underscore.')
                }],
                function(values){
                    frappe.call({
                        method: "whatsapp_integration.api.dashboard_actions.add_device",
                        args: { session_name: values.session_name },
                        callback: function(r) {
                            if (r.message && r.message.error) {
                                frappe.msgprint(r.message.error);
                                return;
                            }
                            if (r.message && r.message.qr) {
                                frappe.msgprint(__('Scan this QR in WhatsApp App for session {0}', [values.session_name]));
                            }
                            frappe.show_alert({ message: __("Device request sent for session {0}", [values.session_name]), indicator: 'green' });
                            frm.reload_doc();
                        }
                    });
                },
                __('Add Device Session'),
                __('Generate')
            );
        }, __('Quick Actions'));

        frm.add_custom_button(__('Send Test Message'), function() {
            frappe.prompt(
                [
                    {fieldtype:'Link', label:__('Session (optional)'), options:'WhatsApp Device', fieldname:'session'},
                    {fieldtype:'Data', label:__('WhatsApp Number (with country code)'), fieldname:'number', reqd:1}
                ],
                function(values){
                    frappe.call({
                        method: "whatsapp_integration.api.dashboard_actions.send_test_message",
                        args: { number: values.number, session: values.session },
                        callback: function() {
                            frappe.show_alert({ message: __('Test message queued'), indicator: 'green' });
                        }
                    });
                },
                __('Send Test Message'),
                __('Send')
            );
        }, __('Quick Actions'));

        frm.add_custom_button(__('Sync Now'), function() {
            frappe.call({
                method: "whatsapp_integration.api.dashboard_actions.sync_now",
                callback: function() {
                    frappe.show_alert({ message: "Dashboard synced", indicator: 'blue' });
                    frm.reload_doc();
                }
            });
        }, __('Quick Actions'));

        // Campaigns
        frm.add_custom_button(__('New Campaign'), function() {
            frappe.new_doc('WhatsApp Campaign');
        }, __('Campaigns'));

        frm.add_custom_button(__('Run Campaign'), function() {
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
                __('Run WhatsApp Campaign'),
                __('Run')
            );
        }, __('Campaigns'));

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
                colors: ['#4caf50', '#ff9800', '#f44336']
            });
        }

        if (frm.doc.node_sessions) {
            const sessions = JSON.parse(frm.doc.node_sessions || "[]");
            if (sessions.length) {
                const rows = sessions.map((session) => {
                    const name = frappe.utils.escape_html(session.session || "default");
                    const status = frappe.utils.escape_html(session.status || __("Unknown"));
                    return `<tr><td>${name}</td><td>${status}</td></tr>`;
                }).join("");
                const table = `
                    <div class="node-session-table">
                        <h4>${__("Node Sessions")}</h4>
                        <table class="table table-bordered" style="margin-top: 10px;">
                            <thead><tr><th>${__("Session")}</th><th>${__("Status")}</th></tr></thead>
                            <tbody>${rows}</tbody>
                        </table>
                    </div>`;
                frm.dashboard.add_section(table);
            } else {
                frm.dashboard.add_comment(__('No Node sessions reported by the service.'));
            }
        }
    }
});
