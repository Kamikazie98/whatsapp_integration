frappe.ui.form.on('WhatsApp Device', {
    refresh: function(frm) {
        // Add Generate/Refresh QR button
        if (!frm.doc.__islocal && frm.doc.status !== 'Connected') {
            frm.add_custom_button(__('Generate QR Code'), function() {
                frappe.call({
                    method: 'generate_qr_code',
                    doc: frm.doc,
                    callback: function(r) {
                        if (r.message) {
                            frm.reload_doc();
                        }
                    }
                });
            }, __('Actions'));
        }
        
        // Add Check Connection Status button
        if (!frm.doc.__islocal) {
            frm.add_custom_button(__('Check Status'), function() {
                frappe.call({
                    method: 'whatsapp_integration.api.whatsapp_real_qr.check_qr_status',
                    args: {
                        session_id: frm.doc.number
                    },
                    callback: function(r) {
                        if (r.message) {
                            const status = r.message;
                            let message = `Status: ${status.status}`;
                            if (status.connected_at) {
                                message += `\\nConnected at: ${status.connected_at}`;
                            }
                            if (status.error) {
                                message += `\\nError: ${status.error}`;
                            }
                            frappe.msgprint({
                                title: __('Connection Status'),
                                message: message
                            });
                        }
                    }
                });
            }, __('Actions'));
        }
        
        // Show QR code in dialog if available
        if (frm.doc.qr_code && frm.doc.status === 'QR Generated') {
            frm.add_custom_button(__('Show QR Code'), function() {
                show_qr_dialog(frm.doc.qr_code, frm.doc.number, frm);
            }, __('Actions'));
        }
        
        // Display QR code inline if available
        if (frm.doc.qr_code && !frm.doc.__islocal) {
            display_qr_inline(frm);
        }
        
        // Auto-refresh for QR Generated devices
        if (frm.doc.status === 'QR Generated' && !frm.doc.__islocal) {
            setup_auto_refresh(frm);
        }
    }
});

function show_qr_dialog(qr_data, device_number, frm) {
    let dialog = new frappe.ui.Dialog({
        title: __('WhatsApp QR Code - {0}', [device_number]),
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'qr_display',
                options: `
                    <div class="text-center">
                        <img src="${qr_data}" style="max-width: 400px; border: 2px solid #28a745; padding: 15px; border-radius: 10px;">
                        <div class="mt-3">
                            <h5 style="color: #28a745;">📱 Scan with WhatsApp Mobile App</h5>
                            <ol class="text-left" style="max-width: 300px; margin: 0 auto;">
                                <li>Open WhatsApp on your phone</li>
                                <li>Go to <strong>Settings</strong> → <strong>Linked Devices</strong></li>
                                <li>Tap <strong>"Link a Device"</strong></li>
                                <li>Point your camera at this QR code</li>
                            </ol>
                            <p class="text-muted mt-3">
                                <small>🔄 This is a REAL WhatsApp QR code - it will connect your device!</small>
                            </p>
                        </div>
                    </div>
                `
            }
        ],
        primary_action_label: __('Check Connection'),
        primary_action: function() {
            frappe.call({
                method: 'whatsapp_integration.api.whatsapp_real_qr.check_qr_status',
                args: {
                    session_id: device_number
                },
                callback: function(r) {
                    if (r.message && r.message.status === 'connected') {
                        frappe.msgprint({
                            title: __('Success!'),
                            message: __('WhatsApp device is now connected!'),
                            indicator: 'green'
                        });
                        dialog.hide();
                        if (frm) {
                            frm.reload_doc();
                        }
                    } else if (r.message && r.message.status === 'qr_ready') {
                        frappe.msgprint(__('QR code is ready for scanning'));
                    } else {
                        frappe.msgprint(__('Device not connected yet. Please scan the QR code.'));
                    }
                }
            });
        },
        secondary_action_label: __('Refresh QR'),
        secondary_action: function() {
            dialog.hide();
            if (frm) {
                frm.call('generate_qr_code').then(() => {
                    frm.reload_doc();
                });
            }
        }
    });
    dialog.show();
}

function display_qr_inline(frm) {
    // Display QR code in the form
    let qr_html = `
        <div class="qr-code-container" style="text-align: center; margin: 20px 0;">
            <img src="${frm.doc.qr_code}" style="max-width: 250px; border: 1px solid #ddd; padding: 10px; border-radius: 5px;">
            <p class="text-muted mt-2">
                <strong>Status:</strong> ${frm.doc.status}<br>
                <small>Scan with WhatsApp mobile app to connect</small>
            </p>
        </div>
    `;
    
    // Add to description field or create a custom section
    if (!$('.qr-code-container').length) {
        $(frm.fields_dict.qr_code.wrapper).append(qr_html);
    }
}

function setup_auto_refresh(frm) {
    // Auto-refresh every 15 seconds for QR Generated devices
    if (frm.auto_refresh_timer) {
        clearInterval(frm.auto_refresh_timer);
    }
    
    frm.auto_refresh_timer = setInterval(function() {
        if (frm.doc.status === 'QR Generated') {
            frappe.call({
                method: 'whatsapp_integration.api.whatsapp_real_qr.check_qr_status',
                args: {
                    session_id: frm.doc.number
                },
                callback: function(r) {
                    if (r.message && r.message.status === 'connected') {
                        clearInterval(frm.auto_refresh_timer);
                        frm.reload_doc();
                    }
                }
            });
        } else {
            clearInterval(frm.auto_refresh_timer);
        }
    }, 15000); // Check every 15 seconds
}

// Clean up timer when form is destroyed
$(document).on('page-change', function() {
    if (cur_frm && cur_frm.auto_refresh_timer) {
        clearInterval(cur_frm.auto_refresh_timer);
    }
});
        }
        
        // Auto-refresh status for newly created devices (with delay)
        if (frm.doc.qr_code && frm.doc.status === 'Disconnected' && !frm.doc.__islocal) {
            setTimeout(function() {
                check_connection_status(frm);
            }, 3000);
        }
    },
    
    after_save: function(frm) {
        // Refresh the form after save to show generated QR code
        if (frm.doc.__islocal === 0) {
            setTimeout(function() {
                frm.reload_doc();
            }, 1000);
        }
    }
});

function show_qr_dialog(qr_data, number) {
    if (!qr_data) {
        frappe.msgprint('QR Code not available. Please refresh.');
        return;
    }
    
    let dialog = new frappe.ui.Dialog({
        title: `QR Code for ${number}`,
        fields: [
            {
                fieldname: 'instructions',
                fieldtype: 'HTML',
                options: `
                    <div style="text-align: center; padding: 20px;">
                        <h4>Scan this QR code with WhatsApp Mobile App</h4>
                        <ol style="text-align: left; display: inline-block;">
                            <li>Open WhatsApp on your phone</li>
                            <li>Tap Menu (3 dots) → Linked devices</li>
                            <li>Tap "Link a device"</li>
                            <li>Point your phone at this screen to capture the QR code</li>
                        </ol>
                        <br>
                        <img src="${qr_data}" style="max-width: 256px; max-height: 256px; border: 1px solid #ddd; padding: 10px;">
                    </div>
                `
            }
        ],
        primary_action_label: 'Close',
        primary_action: function() {
            dialog.hide();
        }
    });
    dialog.show();
}

function display_qr_inline(frm) {
    if (!frm.doc.qr_code) return;
    
    try {
        let qr_html = `
            <div style="text-align: center; margin: 20px 0; padding: 15px; border: 1px solid #d1d8dd; border-radius: 5px;">
                <h5>Scan QR Code with WhatsApp Mobile App</h5>
                <img src="${frm.doc.qr_code}" style="max-width: 200px; max-height: 200px; border: 1px solid #ddd; padding: 10px;">
                <p><small><strong>Status:</strong> ${frm.doc.status}</small></p>
                <p><small>Scan this code with your WhatsApp mobile app to connect</small></p>
            </div>
        `;
        
        // Remove any existing QR display
        $(frm.wrapper).find('.qr-display').remove();
        
        // Add QR display after the form fields
        $(frm.wrapper).find('.form-layout').append(`<div class="qr-display">${qr_html}</div>`);
    } catch (error) {
        console.warn('Error displaying QR code:', error);
    }
}

function check_connection_status(frm) {
    if (!frm || !frm.doc || !frm.doc.number) return;
    
    frappe.call({
        method: 'whatsapp_integration.whatsapp_integration.api.whatsapp_unofficial.check_device_status',
        args: {
            session_id: frm.doc.number
        },
        callback: function(r) {
            try {
                if (r.message && r.message.status === 'Connected') {
                    frm.set_value('status', 'Connected');
                    frm.save();
                    frappe.msgprint('WhatsApp device connected successfully!');
                    // Remove QR display after connection
                    $(frm.wrapper).find('.qr-display').html('<div style="text-align: center; padding: 20px; color: green;"><h4>✅ Device Connected Successfully!</h4><p>You can now send messages through this device.</p></div>');
                } else if (r.message && r.message.status !== 'Error' && r.message.status !== 'Not Started') {
                    // Check again in 10 seconds if not connected and no error
                    setTimeout(function() {
                        if (frm.doc.status !== 'Connected') {
                            check_connection_status(frm);
                        }
                    }, 10000);
                }
            } catch (error) {
                console.warn('Error checking connection status:', error);
            }
        },
        error: function(xhr) {
            console.warn('Failed to check device status:', xhr);
        }
    });
}
