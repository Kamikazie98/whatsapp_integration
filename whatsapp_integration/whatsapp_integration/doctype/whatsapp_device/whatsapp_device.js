frappe.ui.form.on('WhatsApp Device', {
    refresh: function(frm) {
        // Add Generate/Refresh QR button (always show for saved docs)
        if (!frm.doc.__islocal) {
            const button_label = frm.doc.status === 'Connected' ? 'Refresh QR Code' : 'Generate QR Code';
            frm.add_custom_button(__(button_label), function() {
                if (frm.__qr_generating) {
                    frappe.show_alert({ message: __('QR generation in progress...'), indicator: 'blue' }, 3);
                    return;
                }
                frm.__qr_generating = true;
                // Show loading message
                frappe.show_alert({
                    message: __('Generating real WhatsApp QR code (quick method)...'),
                    indicator: 'blue'
                }, 3);
                
                frappe.call({
                    method: 'generate_qr_code',
                    doc: frm.doc,
                    callback: function(r) {
                        frm.__qr_generating = false;
                        if (r.message) {
                            // Reload document to get updated QR data
                            frm.reload_doc().then(() => {
                                // Force refresh of the QR field
                                frm.refresh_field('qr_code');
                                
                                // Show QR code in dialog immediately after generation
                                if (frm.doc.qr_code) {
                                    // Add a small delay to ensure field is updated
                                    setTimeout(() => {
                                        show_qr_dialog(frm.doc.qr_code, frm.doc.number, frm);
                                    }, 500);
                                }
                                frappe.show_alert({
                                    message: __('QR Code generated successfully! Scan the QR code now!'),
                                    indicator: 'green'
                                }, 5);
                            });
                        }
                    },
                    error: function(r) {
                        frm.__qr_generating = false;
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
        
        // Auto-show QR dialog if status is "QR Generated"
        if (frm.doc.status === 'QR Generated' && frm.doc.qr_code && !frm.doc.__islocal) {
            setTimeout(() => {
                show_qr_dialog(frm.doc.qr_code, frm.doc.number, frm);
            }, 1000); // Auto-show QR after 1 second
        }
        
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
    
    let dialog = new frappe.ui.Dialog({
        title: __('WhatsApp QR Code - {0}', [device_number]),
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'qr_display',
                options: `
                    <div class="text-center">
                        <div id="qr-container">
                            <img src="${qr_src}" 
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

    // Poll latest QR (rotates on WhatsApp Web) and connection status while dialog is open
    const pollIntervalMs = 8000; // 8s
    const poll = () => {
        if (!frm || frm.is_new()) return;
        frm.call('get_live_qr').then(r => {
            const msg = r && r.message;
            if (msg && msg.status === 'qr_generated' && msg.qr) {
                const imgEl = dialog.$wrapper.find('#qr-container img')[0];
                if (imgEl) { imgEl.src = msg.qr; }
            }
        }).catch(() => {});
        frm.call('check_node_status').then(r => {
            if (r && r.message && r.message.status === 'connected') {
                clearInterval(dialog.__qr_timer);
                frappe.msgprint({
                    title: __('Connected'),
                    message: __('WhatsApp device is now connected.'),
                    indicator: 'green'
                });
                dialog.hide();
                frm.reload_doc();
            }
        }).catch(() => {});
    };
    dialog.__qr_timer = setInterval(poll, pollIntervalMs);
    // Clean up when dialog closes
    dialog.$wrapper.on('hidden.bs.modal', () => {
        if (dialog.__qr_timer) {
            clearInterval(dialog.__qr_timer);
            dialog.__qr_timer = null;
        }
    });
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
    
    // Remove existing QR container
    $('.custom-qr-container').remove();
    
    // Create a custom QR display section
    let qr_html = `
        <div class="custom-qr-container" style="text-align: center; margin: 20px; padding: 20px; border: 1px solid #ddd; border-radius: 8px; background: #f9f9f9;">
            <h4 style="color: #28a745; margin-bottom: 15px;">📱 WhatsApp QR Code</h4>
            <div style="margin-bottom: 15px;">
                <img src="${qr_src}" 
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
    // Auto-refresh every 12 seconds for QR Generated devices
    if (frm.auto_refresh_timer) {
        clearInterval(frm.auto_refresh_timer);
    }
    
    frm.auto_refresh_timer = setInterval(function() {
        if (frm.doc.status === 'QR Generated') {
            // Pull latest QR and status via server (avoids CORS to Node)
            frm.call('get_live_qr');
            frm.call('check_node_status').then(r => {
                if (r.message && r.message.status === 'connected') {
                    clearInterval(frm.auto_refresh_timer);
                    frm.reload_doc();
                }
            });
        } else {
            clearInterval(frm.auto_refresh_timer);
        }
    }, 12000); // Check every 12 seconds
}

// Clean up timer when form is destroyed
$(document).on('page-change', function() {
    if (cur_frm && cur_frm.auto_refresh_timer) {
        clearInterval(cur_frm.auto_refresh_timer);
    }
});
