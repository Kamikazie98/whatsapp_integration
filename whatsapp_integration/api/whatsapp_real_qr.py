import frappe
import requests
import base64
import io
from PIL import Image
import time
import threading
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import os

# Global storage for active QR sessions
active_qr_sessions = {}

@frappe.whitelist()
def generate_whatsapp_qr(session_id, timeout=30):
    """Generate QR code for WhatsApp Web authentication with better error handling"""
    try:
        frappe.log_error(f"Starting QR generation for session: {session_id}", "WhatsApp QR Debug")
        
        # Check if we already have an active session
        if session_id in active_qr_sessions:
            session_data = active_qr_sessions[session_id]
            if session_data.get('status') == 'qr_ready':
                return {
                    'status': 'qr_generated',
                    'qr': session_data.get('qr_data'),
                    'session': session_id,
                    'message': 'QR code ready for scanning'
                }
        
		# Prepare site context and session directory on main thread
		try:
			site_name = frappe.local.site
		except Exception:
			site_name = None

		# Compute session directory on main thread to avoid frappe calls in thread
		try:
			session_dir = get_session_directory(session_id)
		except Exception as dir_error:
			# get_session_directory already falls back to temp, but we guard anyway
			frappe.log_error(f"Session directory compute failed: {str(dir_error)}", "WhatsApp Session Dir Compute")
			import tempfile
			session_dir = tempfile.mkdtemp(prefix=f"whatsapp_{session_id}_")

		# Start a new QR session in background with prepared context
		start_qr_session(session_id, site_name, session_dir)
        
        # Wait up to timeout seconds for QR to be ready
        wait_time = timeout
        check_interval = 0.5
        checks = int(wait_time / check_interval)
        
        for i in range(checks):
            if session_id in active_qr_sessions:
                session_data = active_qr_sessions[session_id]
                status = session_data.get('status')
                
                if status == 'qr_ready':
                    return {
                        'status': 'qr_generated',
                        'qr': session_data.get('qr_data'),
                        'session': session_id,
                        'message': 'Real WhatsApp QR generated - scan with your phone'
                    }
                elif status == 'connected':
                    return {
                        'status': 'already_connected',
                        'session': session_id,
                        'message': 'WhatsApp is already connected'
                    }
                elif status == 'error':
                    error_msg = session_data.get('error', 'QR generation failed')
                    frappe.log_error(f"QR Error for {session_id}: {error_msg}", "WhatsApp QR Error")
                    raise Exception(error_msg)
            
            time.sleep(check_interval)
        
        # If we get here, it timed out
        frappe.log_error(f"QR generation timed out for session: {session_id}", "WhatsApp QR Timeout")
        raise Exception(f"QR generation timed out after {timeout} seconds")
        
		except Exception as e:
        error_msg = str(e)
        frappe.log_error(f"WhatsApp QR Generation Error: {error_msg}", "WhatsApp Real QR")
        # Clean up failed session
        if session_id in active_qr_sessions:
            del active_qr_sessions[session_id]
        raise Exception(error_msg)

def start_qr_session(session_id, site_name=None, session_dir=None):
	"""Start QR generation session in background thread"""
    if session_id in active_qr_sessions:
        return  # Already started
    
    # Mark as starting
    active_qr_sessions[session_id] = {
        'status': 'starting',
        'started_at': time.time()
    }
    
    # Start in background thread
	thread = threading.Thread(target=capture_whatsapp_qr, args=(session_id, site_name, session_dir))
    thread.daemon = True
    thread.start()

def _safe_log(message, title="WhatsApp QR Thread"):
	"""Thread-safe logger that won't fail if DB logging is unavailable"""
	try:
		frappe.log_error(message, title)
	except Exception:
		try:
			print(f"[{title}] {message}")
		except Exception:
			pass

def capture_whatsapp_qr(session_id, site_name=None, session_dir=None):
	"""Capture real WhatsApp Web QR code"""
    driver = None
    try:
		_safe_log(f"Starting QR capture for session: {session_id}", "WhatsApp QR Capture Start")

		# Ensure frappe site context if available (for logging)
		if site_name:
			try:
				if not getattr(frappe.local, "site", None):
					frappe.init(site=site_name)
					frappe.connect(site=site_name)
			except Exception as ctx_error:
				_safe_log(f"Thread site init failed: {str(ctx_error)}", "WhatsApp QR Thread Init")
        
        # Set up Chrome options
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")  # Use new headless mode
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1280,720")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
		# Set up session directory with error handling (use precomputed dir)
		try:
			if session_dir:
				chrome_options.add_argument(f"--user-data-dir={session_dir}")
				_safe_log(f"Session directory: {session_dir}", "WhatsApp Session Dir")
			else:
				_safe_log("No session_dir passed; continuing without persistence", "WhatsApp Session Dir")
		except Exception as dir_error:
			_safe_log(f"Session directory error: {str(dir_error)}", "WhatsApp Session Dir Error")
			# Continue without session persistence if adding arg fails
        
        # Create driver with timeout
        try:
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.set_page_load_timeout(15)
			_safe_log(f"Chrome driver created successfully", "WhatsApp Chrome Success")
        except Exception as driver_error:
			_safe_log(f"Chrome driver creation failed: {str(driver_error)}", "WhatsApp Chrome Error")
            raise Exception(f"Failed to start Chrome: {str(driver_error)}")
        
        # Navigate to WhatsApp Web
        try:
            driver.get("https://web.whatsapp.com")
			_safe_log(f"Navigated to WhatsApp Web", "WhatsApp Navigation")
        except Exception as nav_error:
			_safe_log(f"Navigation failed: {str(nav_error)}", "WhatsApp Navigation Error")
            raise Exception(f"Failed to load WhatsApp Web: {str(nav_error)}")
        
        # Wait for page to load
        time.sleep(3)
        
        # Check if already connected
        try:
            chat_list = driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-list"]')
            if chat_list:
                active_qr_sessions[session_id] = {
                    'status': 'connected',
                    'message': 'Already connected to WhatsApp'
                }
				_safe_log(f"Already connected", "WhatsApp Already Connected")
                return
        except:
            pass  # Not connected, continue to QR
        
        # Wait for QR code with multiple selectors
        qr_selectors = [
            '[data-ref] canvas',
            'canvas[aria-label*="QR"]',
            'div[data-ref] canvas',
            'canvas'
        ]
        
        qr_element = None
        wait = WebDriverWait(driver, 10)
        
        for selector in qr_selectors:
            try:
                qr_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                if qr_element:
                    # Verify it's actually a QR code by checking size
                    size = qr_element.size
                    if size['width'] > 200 and size['height'] > 200:
							_safe_log(f"QR element found with selector: {selector}", "WhatsApp QR Found")
                        break
            except Exception as selector_error:
					_safe_log(f"Selector {selector} failed: {str(selector_error)}", "WhatsApp QR Selector")
                continue
        
        if not qr_element:
            raise Exception("QR code element not found with any selector")
        
        # Take screenshot of QR area with some padding
        location = qr_element.location
        size = qr_element.size
        
        # Add padding around QR code
        padding = 20
        left = max(0, location['x'] - padding)
        top = max(0, location['y'] - padding)
        width = size['width'] + (padding * 2)
        height = size['height'] + (padding * 2)
        
        # Take full screenshot
        screenshot = driver.get_screenshot_as_png()
        image = Image.open(io.BytesIO(screenshot))
        
        # Crop QR area
        qr_image = image.crop((left, top, left + width, top + height))
        
        # Enhance QR image
        qr_image = qr_image.convert('RGB')
        
        # Convert to base64
        buffer = io.BytesIO()
        qr_image.save(buffer, format='PNG', quality=95)
        img_str = base64.b64encode(buffer.getvalue()).decode()
        qr_data_url = f"data:image/png;base64,{img_str}"
        
        # Update session status
        active_qr_sessions[session_id] = {
            'status': 'qr_ready',
            'qr_data': qr_data_url,
            'generated_at': time.time(),
            'driver_active': True
        }
        
		_safe_log(f"QR capture successful for session: {session_id}", "WhatsApp QR Success")
        
        # Keep driver alive for a while to detect scan
        monitor_qr_scan(driver, session_id)
        
    except Exception as e:
        error_msg = str(e)
		_safe_log(f"QR Capture Error for {session_id}: {error_msg}", "WhatsApp QR Capture")
        active_qr_sessions[session_id] = {
            'status': 'error',
            'error': error_msg
        }
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def monitor_qr_scan(driver, session_id, timeout=300):
    """Monitor for QR scan and connection"""
    try:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # Check if connected (chat list appears)
                chat_list = driver.find_element(By.CSS_SELECTOR, '[data-testid="chat-list"]')
                if chat_list:
                    active_qr_sessions[session_id] = {
                        'status': 'connected',
                        'connected_at': time.time(),
                        'message': 'Successfully connected to WhatsApp'
                    }
                    return
            except:
                pass
            
            # Check if QR expired or changed
            try:
                qr_element = driver.find_element(By.CSS_SELECTOR, '[data-ref] canvas')
                if not qr_element:
                    # QR might have expired, generate new one
                    break
            except:
                break
            
            time.sleep(2)
            
    except Exception as e:
		_safe_log(f"QR Monitor Error: {str(e)}", "WhatsApp QR Monitor")

def get_session_directory(session_id):
    """Get session directory for Chrome profile"""
    try:
        # Use frappe.get_site_path() method correctly
        private_files = frappe.get_site_path('private', 'files')
        session_dir = os.path.join(private_files, 'whatsapp_sessions', session_id)
        os.makedirs(session_dir, exist_ok=True)
        return session_dir
    except Exception as e:
        # Fallback to temp directory if site path fails
        import tempfile
        temp_dir = tempfile.mkdtemp(prefix=f"whatsapp_{session_id}_")
		_safe_log(f"Site path failed, using temp: {str(e)}", "WhatsApp Session Dir")
        return temp_dir

@frappe.whitelist()
def check_qr_status(session_id):
    """Check status of QR generation"""
    if session_id in active_qr_sessions:
        return active_qr_sessions[session_id]
    else:
        return {'status': 'not_found'}

@frappe.whitelist()
def cleanup_session(session_id):
    """Clean up QR session"""
    if session_id in active_qr_sessions:
        del active_qr_sessions[session_id]
    return {'success': True}

@frappe.whitelist()
def health_check_real():
    """Health check for real QR service"""
    try:
        # Test Chrome availability
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.quit()
        
        return {
            'status': 'WhatsApp Real QR Service Ready',
            'chrome_available': True,
            'active_sessions': len(active_qr_sessions),
            'version': '3.0.0 - Real QR',
            'timestamp': frappe.utils.now()
        }
    except Exception as e:
        return {
            'status': 'Chrome Setup Failed',
            'chrome_available': False,
            'error': str(e),
            'timestamp': frappe.utils.now()
        }
