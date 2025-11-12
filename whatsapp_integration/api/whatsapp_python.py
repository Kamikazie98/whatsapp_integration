import frappe
import qrcode
import io
import base64
from PIL import Image
import threading
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
import os

# Global storage for WebDriver instances and QR codes
drivers = {}
qr_codes = {}
connection_status = {}

def get_session_directory(session_id):
    """Get session directory for Chrome profile"""
    private_files = frappe.get_site_path('private', 'files')
    session_dir = os.path.join(private_files, 'whatsapp_sessions', session_id)
    os.makedirs(session_dir, exist_ok=True)
    return session_dir

@frappe.whitelist()
def generate_qr_code(session_id, timeout=30):
    """Generate QR code for WhatsApp Web authentication with shorter timeout"""
    try:
        # Quick timeout for Chrome setup
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        
        # Set up session directory
        session_dir = get_session_directory(session_id)
        chrome_options.add_argument(f"--user-data-dir={session_dir}")
        
        # Quick Chrome driver setup with shorter timeout
        from webdriver_manager.chrome import ChromeDriverManager
        service = ChromeService(ChromeDriverManager().install())
        
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(15)  # Shorter timeout
        
        try:
            # Navigate to WhatsApp Web with timeout
            driver.get("https://web.whatsapp.com")
            
            # Wait for QR code with shorter timeout
            qr_selector = 'div[data-ref] canvas'
            wait = WebDriverWait(driver, 10)  # Reduced from 30 to 10 seconds
            
            qr_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, qr_selector)))
            
            # Take screenshot of QR code area
            qr_location = qr_element.location
            qr_size = qr_element.size
            
            # Take screenshot
            screenshot = driver.get_screenshot_as_png()
            image = Image.open(io.BytesIO(screenshot))
            
            # Crop QR code area
            left = qr_location['x']
            top = qr_location['y'] 
            right = left + qr_size['width']
            bottom = top + qr_size['height']
            
            qr_image = image.crop((left, top, right, bottom))
            
            # Convert to base64
            buffer = io.BytesIO()
            qr_image.save(buffer, format='PNG')
            img_str = base64.b64encode(buffer.getvalue()).decode()
            
            qr_data_url = f"data:image/png;base64,{img_str}"
            
            return {
                'status': 'qr_generated',
                'qr': qr_data_url,
                'session': session_id
            }
            
        except TimeoutException:
            frappe.log_error("WhatsApp QR code timeout", "WhatsApp QR Timeout")
            raise Exception("QR code generation timed out")
            
        finally:
            try:
                driver.quit()
            except:
                pass
                
    except Exception as e:
        frappe.log_error(f"WhatsApp QR Generation Error: {str(e)}", "WhatsApp QR Error")
        raise Exception(f"QR code generation timed out")

def start_whatsapp_session(session_id):
    """Start WhatsApp Web session and capture QR code"""
    try:
        # Setup Chrome options - make headless optional for testing
        chrome_options = Options()
        
        # Only use headless in production
        if frappe.conf.get('developer_mode') != 1:
            chrome_options.add_argument("--headless")
            
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # Create profile directory for session persistence
        profile_dir = os.path.join(frappe.get_site_path(), "private", "files", "whatsapp_sessions", session_id)
        os.makedirs(profile_dir, exist_ok=True)
        chrome_options.add_argument(f"--user-data-dir={profile_dir}")
        
        # Initialize Chrome driver
        driver = webdriver.Chrome(
            service=webdriver.chrome.service.Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        drivers[session_id] = driver
        
        # Navigate to WhatsApp Web
        driver.get("https://web.whatsapp.com")
        
        # Wait for QR code or main interface
        wait = WebDriverWait(driver, 30)
        
        try:
            # Look for QR code
            qr_element = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-ref] canvas, [data-testid='qr-code'] canvas"))
            )
            
            # Capture QR code
            qr_code_data = driver.execute_script("""
                var canvas = document.querySelector('[data-ref] canvas') || document.querySelector('[data-testid="qr-code"] canvas');
                if (canvas) {
                    return canvas.toDataURL('image/png');
                }
                return null;
            """)
            
            if qr_code_data:
                qr_codes[session_id] = qr_code_data
                connection_status[session_id] = 'Waiting for scan'
                
                # Monitor for connection
                monitor_connection(driver, session_id)
            else:
                # Maybe already logged in
                try:
                    # Check if we're already in the main interface
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='chat-list']")))
                    connection_status[session_id] = 'Connected'
                    qr_codes[session_id] = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='  # 1x1 transparent pixel
                except TimeoutException:
                    connection_status[session_id] = 'Error'
                    
        except TimeoutException:
            connection_status[session_id] = 'Error'
            frappe.log_error("WhatsApp Web QR code not found", "WhatsApp Session")
            
    except Exception as e:
        connection_status[session_id] = 'Error'
        frappe.log_error(f"WhatsApp session error for {session_id}: {str(e)}", "WhatsApp Session")

def monitor_connection(driver, session_id):
    """Monitor WhatsApp connection status"""
    try:
        wait = WebDriverWait(driver, 60)  # Wait up to 1 minute for scan
        
        # Wait for main interface to appear (indicates successful login)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='chat-list']")))
        
        connection_status[session_id] = 'Connected'
        # Clear QR code after successful connection
        if session_id in qr_codes:
            del qr_codes[session_id]
            
        # Keep session alive
        keep_session_alive(driver, session_id)
        
    except TimeoutException:
        connection_status[session_id] = 'QR Expired'
    except Exception as e:
        connection_status[session_id] = 'Error'
        frappe.log_error(f"Connection monitoring error for {session_id}: {str(e)}", "WhatsApp Monitor")

def keep_session_alive(driver, session_id):
    """Keep WhatsApp session alive"""
    try:
        while connection_status.get(session_id) == 'Connected':
            time.sleep(30)  # Check every 30 seconds
            
            # Check if still connected
            try:
                driver.find_element(By.CSS_SELECTOR, "[data-testid='chat-list']")
            except NoSuchElementException:
                connection_status[session_id] = 'Disconnected'
                break
                
    except Exception as e:
        connection_status[session_id] = 'Error'
        frappe.log_error(f"Session keep-alive error for {session_id}: {str(e)}", "WhatsApp KeepAlive")

@frappe.whitelist()
def check_session_status(session_id):
    """Check WhatsApp session status"""
    status = connection_status.get(session_id, 'Not Started')
    return {
        'session': session_id,
        'status': status
    }

@frappe.whitelist()
def send_message(session_id, phone_number, message):
    """Send WhatsApp message"""
    try:
        if session_id not in drivers or connection_status.get(session_id) != 'Connected':
            return {
                'success': False,
                'error': 'Session not connected'
            }
        
        driver = drivers[session_id]
        
        # Navigate to chat with phone number
        chat_url = f"https://web.whatsapp.com/send?phone={phone_number}&text={message}"
        driver.get(chat_url)
        
        wait = WebDriverWait(driver, 10)
        
        # Wait for and click send button
        send_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='send']"))
        )
        send_button.click()
        
        return {
            'success': True,
            'message_id': f"{session_id}_{phone_number}_{int(time.time())}",
            'timestamp': frappe.utils.now()
        }
        
    except Exception as e:
        frappe.log_error(f"Send message error for {session_id}: {str(e)}", "WhatsApp Send Message")
        return {
            'success': False,
            'error': str(e)
        }

@frappe.whitelist()
def close_session(session_id):
    """Close WhatsApp session"""
    try:
        if session_id in drivers:
            drivers[session_id].quit()
            del drivers[session_id]
        
        if session_id in qr_codes:
            del qr_codes[session_id]
            
        if session_id in connection_status:
            del connection_status[session_id]
            
        return {'success': True}
        
    except Exception as e:
        frappe.log_error(f"Close session error for {session_id}: {str(e)}", "WhatsApp Close Session")
        return {'success': False, 'error': str(e)}

@frappe.whitelist(allow_guest=True)
def health_check():
    """Health check endpoint"""
    return {
        'status': 'WhatsApp Python Service Running',
        'version': '1.0.0',
        'active_sessions': len(drivers),
        'timestamp': frappe.utils.now()
    }
