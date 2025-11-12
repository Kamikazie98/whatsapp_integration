frappe.ui.form.on('WhatsApp Device', {
    refresh: function(frm) {
        // Add Generate/Refresh QR button (always show for saved docs)
        if (!frm.doc.__islocal) {
            const button_label = frm.doc.status === 'Connected' ? 'Refresh QR Code' : 'Generate QR Code';
            frm.add_custom_button(__(button_label), function() {
                // Show loading message
                frappe.show_alert({
                    message: __('Generating real WhatsApp QR code (quick method)...'),
                    indicator: 'blue'
                }, 3);
                
                frappe.call({
                    method: 'generate_qr_code',
                    doc: frm.doc,
                    callback: function(r) {
                        if (r.message) {
                            // Reload document to get updated QR data
                            frm.reload_doc().then(() => {
                                // Force refresh of the QR field
                                frm.refresh_field('qr_code');
                                
                // Show QR code in dialog immediately after generation (live updates)
                // Always open dialog; image will live-update if QR not ready yet
                setTimeout(() => {
                    show_qr_dialog(frm.doc.qr_code || '', frm.doc.number, frm);
                }, 500);
                                frappe.show_alert({
                                    message: __('QR Code generated successfully! Scan the QR code now!'),
                                    indicator: 'green'
                                }, 5);
                            });
                        }
                    },
                    error: function(r) {
                        frappe.show_alert({
                            message: __('Failed to generate QR code. Check console for details.'),
                            indicator: 'red'
                        }, 5);
                    }
                });
            }, __('Actions'));
        }
        
        // Add Show QR Code button if QR exists
        if (!frm.doc.__islocal && frm.doc.qr_code) {
            frm.add_custom_button(__('Show QR Code'), function() {
                show_qr_dialog(frm.doc.qr_code, frm.doc.number, frm);
            }, __('Actions'));
        }
        
        // Debug button to check QR data (remove after testing)
        if (!frm.doc.__islocal) {
            frm.add_custom_button(__('Debug QR'), function() {
                console.log('QR Code data:', frm.doc.qr_code);
                console.log('QR Code length:', frm.doc.qr_code ? frm.doc.qr_code.length : 0);
                frappe.msgprint({
                    title: 'QR Debug Info',
                    message: `
                        <p><strong>QR Code exists:</strong> ${!!frm.doc.qr_code}</p>
                        <p><strong>QR Code length:</strong> ${frm.doc.qr_code ? frm.doc.qr_code.length : 0}</p>
                        <p><strong>QR Code starts with:</strong> ${frm.doc.qr_code ? frm.doc.qr_code.substring(0, 50) : 'N/A'}</p>
                        <p><strong>Status:</strong> ${frm.doc.status}</p>
                    `
                });
            }, __('Actions'));
        }
        
        // Add Check Connection Status button
        if (!frm.doc.__islocal) {
            frm.add_custom_button(__('Check Status'), function() {
                frappe.call({
                    method: 'check_connection_status',
                    doc: frm.doc,
                    callback: function(r) {
                        if (r.message) {
                            const status = r.message;
                            let message = `Connection Status: ${status.status}`;
                            if (status.message) {
                                message += `\n\n${status.message}`;
                            }
                            if (status.last_sync) {
                                message += `\nLast Sync: ${status.last_sync}`;
                            }
                            frappe.msgprint({
                                title: __('WhatsApp Connection Status'),
                                message: message,
                                indicator: status.status === 'connected' ? 'green' : 
                                          status.status === 'qr_generated' ? 'blue' : 'red'
                            });
                        }
                    },
                    error: function(r) {
                        frappe.msgprint({
                            title: __('Connection Status Error'),
                            message: 'Failed to check connection status. Please try again.',
                            indicator: 'red'
                        });
                    }
                });
            }, __('Actions'));

            // Add Sync Status button (updates DocType from real session)
            frm.add_custom_button(__('Sync Status'), function() {
                frm.call('sync_status').then((res) => {
                    const msg = (res && res.message && res.message.message) || __('Status synced');
                    frappe.show_alert({ message: msg, indicator: 'green' }, 5);
                    frm.reload_doc();
                }).catch(() => {
                    frappe.show_alert({ message: __('Failed to sync status'), indicator: 'red' }, 5);
                });
            }, __('Actions'));
        }
        
        // Add Troubleshoot button for linking issues
        if (!frm.doc.__islocal) {
            frm.add_custom_button(__('Troubleshoot Linking'), function() {
                frappe.msgprint({
                    title: __('WhatsApp Device Linking Troubleshooting'),
                    message: `
                        <div style="text-align: left;">
                            <h5>If you see "Can't link new devices at this time":</h5>
                            <ol>
                                <li><strong>Wait 2-5 minutes</strong> between QR scan attempts</li>
                                <li><strong>Check device limit:</strong> WhatsApp allows maximum 4 linked devices</li>
                                <li><strong>Unlink old devices:</strong> Go to WhatsApp → Settings → Linked Devices → Remove unused devices</li>
                                <li><strong>Generate fresh QR:</strong> Click "Generate Fresh QR" button</li>
                                <li><strong>Try different network:</strong> Switch between WiFi/Mobile data</li>
                                <li><strong>Restart WhatsApp</strong> on your phone completely</li>
                            </ol>
                            <div style="background: #f8f9fa; padding: 10px; border-radius: 5px; margin-top: 10px;">
                                <strong>Best Practice:</strong> Wait at least 2-3 minutes between attempts. WhatsApp has rate limiting.
                            </div>
                        </div>
                    `,
                    indicator: 'blue'
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
        
        // Auto-show disabled to avoid duplicate dialogs; dialog opens after generation callback
        
        // Auto-refresh for QR Generated devices
        if (frm.doc.status === 'QR Generated' && !frm.doc.__islocal) {
            setup_auto_refresh(frm);
        }
    }
});

function show_qr_dialog(qr_data, device_number, frm) {
    // Handle different QR data formats
    let qr_src = qr_data;
    
    // If QR data starts with data:image, use it directly
    if (qr_data && qr_data.startsWith('data:image/')) {
        qr_src = qr_data;
    } 
    // If it's a file path, make sure it's accessible
    else if (qr_data && !qr_data.startsWith('http') && !qr_data.startsWith('/files/')) {
        qr_src = '/files/' + qr_data;
    }
    // If empty, use a 1x1 transparent placeholder
    if (!qr_src) {
        qr_src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
    }
    
    let dialog = new frappe.ui.Dialog({
        title: __('WhatsApp QR Code - {0}', [device_number]),
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'qr_display',
                options: `
                    <div class="text-center">
                        <div id="qr-container">
                            <img id="qr-dialog-img" src="${qr_src}" 
                                 style="max-width: 400px; border: 2px solid #28a745; padding: 15px; border-radius: 10px;"
                                 onerror="this.style.display='none'; document.getElementById('qr-error').style.display='block';">
                            <div id="qr-error" style="display: none; color: red; padding: 20px;">
                                <p>QR Code not found or cannot be displayed</p>
                                <p><small>QR Source: ${qr_src}</small></p>
                                <button onclick="location.reload()" class="btn btn-sm btn-primary">Refresh Page</button>
                            </div>
                        </div>
                        <div class="mt-3">
                            <h5 style="color: #28a745;">📱 Scan with WhatsApp Mobile App</h5>
                            <div style="background: #fff3cd; border: 1px solid #ffeaa7; padding: 10px; border-radius: 5px; margin: 10px 0;">
                                <p style="margin: 0; color: #856404;"><strong>⚠️ If you see "Can't link new devices":</strong></p>
                                <ul style="margin: 5px 0; color: #856404; font-size: 12px;">
                                    <li>Wait 2-5 minutes and try again</li>
                                    <li>Make sure you have fewer than 4 linked devices</li>
                                    <li>Try scanning from WhatsApp Settings → Linked Devices → Link a Device</li>
                                    <li>Refresh this page and generate a new QR code</li>
                                </ul>
                            </div>
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
                method: 'check_connection_status',
                doc: frm.doc,
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
                    } else {
                        frappe.msgprint({
                            title: __('Not Connected Yet'),
                            message: __('Please scan the QR code with your phone first.'),
                            indicator: 'blue'
                        });
                    }
                }
            });
        },
        secondary_action_label: __('Generate Fresh QR'),
        secondary_action: function() {
            dialog.hide();
            frappe.show_alert({
                message: __('Generating fresh QR code...'),
                indicator: 'blue'
            }, 3);
            if (frm) {
                frm.call('generate_qr_code').then(() => {
                    setTimeout(() => {
                        frm.reload_doc();
                    }, 2000);
                });
            }
        }
    });
    dialog.show();

    // Live update QR image every 3 seconds while dialog open
    const poll = () => {
        frappe.call({
            method: 'whatsapp_integration.api.whatsapp_playwright.check_qr_status_pw',
            args: { session_id: device_number },
            callback: function(r) {
                const data = r.message || {};
                if (data.status === 'connected') {
                    // Connected: stop polling and refresh form
                    if (dialog.__qr_timer) clearInterval(dialog.__qr_timer);
                    dialog.hide();
                    if (frm) frm.reload_doc();
                    return;
                }
                if (data.qr_data) {
                    const img = dialog.$wrapper.find('#qr-dialog-img');
                    if (img && img.length) {
                        const current = img.attr('src');
                        if (current !== data.qr_data) {
                            img.attr('src', data.qr_data);
                        }
                    }
                }
            }
        });
    };
    dialog.__qr_timer = setInterval(poll, 3000);
    dialog.$wrapper.on('hidden.bs.modal', () => {
        if (dialog.__qr_timer) clearInterval(dialog.__qr_timer);
    });
}

// Ensure global access for inline onclick handlers
if (typeof window !== 'undefined') {
    window.show_qr_dialog = show_qr_dialog;
}

function display_qr_inline(frm) {
    // Handle different QR data formats
    let qr_src = frm.doc.qr_code;
    
    // If QR data starts with data:image, use it directly
    if (qr_src && qr_src.startsWith('data:image/')) {
        // Use as is
    } 
    // If it's a file path, make sure it's accessible
    else if (qr_src && !qr_src.startsWith('http') && !qr_src.startsWith('/files/')) {
        qr_src = '/files/' + qr_src;
    }
    // If empty, use a 1x1 transparent placeholder
    if (!qr_src) {
        qr_src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';
    }
    
    // Remove existing QR container
    $('.custom-qr-container').remove();
    
    // Create a custom QR display section
    let qr_html = `
        <div class="custom-qr-container" style="text-align: center; margin: 20px; padding: 20px; border: 1px solid #ddd; border-radius: 8px; background: #f9f9f9;">
            <h4 style="color: #28a745; margin-bottom: 15px;">📱 WhatsApp QR Code</h4>
            <div style="margin-bottom: 15px;">
                <img id="qr-inline-img" src="${qr_src}" 
                     style="max-width: 300px; border: 2px solid #28a745; padding: 15px; border-radius: 10px; background: white;"
                     onerror="this.style.display='none'; this.nextElementSibling.style.display='block';">
                <div style="display: none; color: red; padding: 20px;">
                    <p>QR Code cannot be displayed</p>
                    <p><small>Source: ${qr_src}</small></p>
                    <button onclick="location.reload()" class="btn btn-sm btn-primary">Refresh Page</button>
                </div>
            </div>
            <div style="margin-top: 10px;">
                <div style="background: #fff3cd; border: 1px solid #ffeaa7; padding: 8px; border-radius: 5px; margin: 10px 0;">
                    <p style="margin: 0; color: #856404; font-size: 12px;"><strong>⚠️ If scanning fails:</strong></p>
                    <ul style="margin: 5px 0; color: #856404; font-size: 11px; padding-left: 15px;">
                        <li>Wait 2-5 minutes between attempts</li>
                        <li>Check you have fewer than 4 linked devices</li>
                        <li>Generate a fresh QR code</li>
                    </ul>
                </div>
                <p class="text-muted">
                    <strong>Status:</strong> <span style="color: ${frm.doc.status === 'Connected' ? 'green' : frm.doc.status === 'QR Generated' ? 'orange' : 'red'}">${frm.doc.status}</span><br>
                    <small>🔄 This is a REAL WhatsApp QR code - scan to connect!</small>
                </p>
                <div style="margin-top: 10px;">
                    <button class="btn btn-sm btn-success" onclick="show_qr_dialog('${qr_src}', '${frm.doc.number}', cur_frm)" style="margin-right: 5px;">
                        🔍 Show Large QR Code
                    </button>
                    <button class="btn btn-sm btn-warning" onclick="cur_frm.call('generate_qr_code'); setTimeout(() => location.reload(), 2000);">
                        🔄 Generate Fresh QR
                    </button>
                </div>
            </div>
        </div>
    `;
    
    // Add after the QR code field
    $(frm.fields_dict.qr_code.wrapper).after(qr_html);
}

function setup_auto_refresh(frm) {
    // Auto-refresh every 3 seconds for QR updates and connection status
    if (frm.auto_refresh_timer) {
        clearInterval(frm.auto_refresh_timer);
    }
    
    frm.auto_refresh_timer = setInterval(function() {
        if (frm.doc.status === 'QR Generated') {
            frappe.call({
                method: 'whatsapp_integration.api.whatsapp_playwright.check_qr_status_pw',
                args: {
                    session_id: frm.doc.number
                },
                callback: function(r) {
                    const data = r.message || {};
                    if (data.status === 'connected') {
                        clearInterval(frm.auto_refresh_timer);
                        frm.reload_doc();
                        return;
                    }
                    if (data.qr_data) {
                        const img = $(frm.wrapper).find('#qr-inline-img');
                        if (img && img.length) {
                            const current = img.attr('src');
                            if (current !== data.qr_data) {
                                img.attr('src', data.qr_data);
                            }
                        }
                    }
                }
            });
        } else {
            clearInterval(frm.auto_refresh_timer);
        }
    }, 3000); // Check every 3 seconds
}

// Clean up timer when form is destroyed
$(document).on('page-change', function() {
    if (cur_frm && cur_frm.auto_refresh_timer) {
        clearInterval(cur_frm.auto_refresh_timer);
    }
});
