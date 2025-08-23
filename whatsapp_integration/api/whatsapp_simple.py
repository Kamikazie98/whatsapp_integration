import frappe
import qrcode
import io
import base64
from PIL import Image

@frappe.whitelist()
def generate_simple_qr_code(session_id):
    """Generate a simple QR code for testing (without WhatsApp Web integration)"""
    try:
        # For now, generate a placeholder QR that points to WhatsApp Web
        whatsapp_url = f"https://web.whatsapp.com/"
        
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(whatsapp_url)
        qr.make(fit=True)
        
        # Create QR code image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to base64 data URL
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        
        qr_data_url = f"data:image/png;base64,{img_str}"
        
        return {
            'status': 'qr_generated',
            'qr': qr_data_url,
            'session': session_id,
            'message': 'Simple QR generated. Please visit https://web.whatsapp.com manually to scan.'
        }
        
    except Exception as e:
        frappe.log_error(f"Simple QR Generation Error: {str(e)}", "WhatsApp Simple QR")
        return {
            'status': 'error',
            'message': str(e)
        }

@frappe.whitelist(allow_guest=True)
def health_check():
    """Health check endpoint for Python service"""
    return {
        'status': 'WhatsApp Python Service Running',
        'version': '2.0.0 - Simplified',
        'chrome_available': check_chrome_available(),
        'timestamp': frappe.utils.now()
    }

def check_chrome_available():
    """Check if Chrome is available for Selenium"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
        
        # Try to get Chrome driver path
        ChromeDriverManager().install()
        return True
    except Exception:
        return False

# Simple status tracking without Selenium
simple_sessions = {}

@frappe.whitelist()
def check_simple_session_status(session_id):
    """Check session status (simplified version)"""
    status = simple_sessions.get(session_id, 'Not Started')
    return {
        'session': session_id,
        'status': status
    }

@frappe.whitelist()
def set_session_connected(session_id):
    """Manually set session as connected (for testing)"""
    simple_sessions[session_id] = 'Connected'
    return {'success': True, 'status': 'Connected'}
