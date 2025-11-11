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
def generate_whatsapp_qr(session_id, timeout=30, headless=True):
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
        start_qr_session(session_id, site_name, session_dir, headless=headless)
        
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

def start_qr_session(session_id, site_name=None, session_dir=None, headless=True):
    """Start QR generation session in background thread"""
    if session_id in active_qr_sessions:
        return  # Already started
    
    # Mark as starting
    active_qr_sessions[session_id] = {
        'status': 'starting',
        'started_at': time.time()
    }
    
    # Start in background thread
    thread = threading.Thread(target=capture_whatsapp_qr, args=(session_id, site_name, session_dir, headless))
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

def capture_whatsapp_qr(session_id, site_name=None, session_dir=None, headless=True):
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
        if headless:
            # Prefer new headless; we may fallback later if startup fails
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1280,720")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        # Networking hardening
        chrome_options.add_argument("--no-proxy-server")
        chrome_options.add_argument("--proxy-bypass-list=*")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--enable-features=NetworkServiceInProcess")
        chrome_options.add_argument("--disable-features=SameSiteByDefaultCookies,CookieDeprecationLabels,PrivacySandboxSettings2")
        # Permissions and prefs
        chrome_prefs = {
            "profile.default_content_setting_values.notifications": 1,
            "profile.default_content_setting_values.images": 1,
            "profile.block_third_party_cookies": False,
        }
        chrome_options.add_experimental_option("prefs", chrome_prefs)
        # Improve compatibility with WhatsApp Web
        chrome_options.add_argument("--lang=en-US,en")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
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
            # Allow custom Chrome binary via env
            import os as _os
            chrome_binary = _os.environ.get("CHROME_BINARY")
            if chrome_binary:
                chrome_options.binary_location = chrome_binary
                _safe_log(f"Using custom Chrome binary at {chrome_binary}", "WhatsApp Chrome Binary")

            # Prefer explicit chromedriver path if provided
            chromedriver_path = _os.environ.get("CHROMEDRIVER_PATH")
            chromedriver_version = _os.environ.get("CHROMEDRIVER_VERSION")

            if chromedriver_path:
                service = ChromeService(executable_path=chromedriver_path)
                _safe_log(f"Using CHROMEDRIVER_PATH {chromedriver_path}", "WhatsApp ChromeDriver")
            else:
                if chromedriver_version:
                    service = ChromeService(ChromeDriverManager(version=chromedriver_version).install())
                    _safe_log(f"Using ChromeDriverManager version={chromedriver_version}", "WhatsApp ChromeDriver")
                else:
                    service = ChromeService(ChromeDriverManager().install())

            driver = webdriver.Chrome(service=service, options=chrome_options)
            driver.set_page_load_timeout(15)
            _safe_log(f"Chrome driver created successfully", "WhatsApp Chrome Success")

            # Reduce automation detectability
            try:
                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                    "source": """
                        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                        window.chrome = { runtime: {} };
                        const originalQuery = window.navigator.permissions.query;
                        window.navigator.permissions.query = (parameters) => (
                          parameters.name === 'notifications' ?
                            Promise.resolve({ state: Notification.permission }) :
                            originalQuery(parameters)
                        );
                        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                        Object.defineProperty(navigator, 'language', { get: () => 'en-US' });
                        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                    """
                })
            except Exception as harden_err:
                _safe_log(f"Hardening script injection failed: {str(harden_err)}", "WhatsApp Chrome Hardening")
        except Exception as driver_error:
            _safe_log(f"Chrome driver creation failed (first attempt): {str(driver_error)}", "WhatsApp Chrome Error")
            # If headless new fails, try legacy headless once
            if headless:
                try:
                    _safe_log("Retrying with legacy --headless flag", "WhatsApp Chrome Retry")
                    chrome_options = Options()
                    chrome_options.add_argument("--headless")
                    chrome_options.add_argument("--no-sandbox")
                    chrome_options.add_argument("--disable-dev-shm-usage")
                    chrome_options.add_argument("--disable-gpu")
                    chrome_options.add_argument("--window-size=1280,720")
                    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
                    chrome_options.add_experimental_option('useAutomationExtension', False)
                    chrome_options.add_argument("--no-proxy-server")
                    chrome_options.add_argument("--proxy-bypass-list=*")
                    chrome_options.add_argument("--ignore-certificate-errors")
                    chrome_options.add_argument("--allow-running-insecure-content")
                    chrome_options.add_argument("--enable-features=NetworkServiceInProcess")
                    chrome_options.add_argument("--disable-features=SameSiteByDefaultCookies,CookieDeprecationLabels,PrivacySandboxSettings2")
                    chrome_prefs = {
                        "profile.default_content_setting_values.notifications": 1,
                        "profile.default_content_setting_values.images": 1,
                        "profile.block_third_party_cookies": False,
                    }
                    chrome_options.add_experimental_option("prefs", chrome_prefs)
                    chrome_options.add_argument("--lang=en-US,en")
                    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

                    if session_dir:
                        chrome_options.add_argument(f"--user-data-dir={session_dir}")

                    if chrome_binary:
                        chrome_options.binary_location = chrome_binary

                    if chromedriver_path:
                        service = ChromeService(executable_path=chromedriver_path)
                    else:
                        if chromedriver_version:
                            service = ChromeService(ChromeDriverManager(version=chromedriver_version).install())
                        else:
                            service = ChromeService(ChromeDriverManager().install())

                    driver = webdriver.Chrome(service=service, options=chrome_options)
                    driver.set_page_load_timeout(15)
                except Exception as retry_err:
                    _safe_log(f"Legacy headless retry failed: {str(retry_err)}", "WhatsApp Chrome Error")
                    raise Exception(f"Failed to start Chrome: {str(driver_error)}")
            else:
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
        
        # Prefer exact QR pixels from canvas to avoid scan issues
        qr_data_url = None
        try:
            qr_data_url = driver.execute_script("""
                const c = document.querySelector('[data-ref] canvas') || document.querySelector('canvas[aria-label*="QR"]') || document.querySelector('div[data-ref] canvas') || document.querySelector('canvas');
                if (!c) return null;
                try { return c.toDataURL('image/png'); } catch (e) { return null; }
            """)
        except Exception as js_err:
            _safe_log(f"Canvas toDataURL failed: {str(js_err)}", "WhatsApp QR Canvas")

        if not qr_data_url or not isinstance(qr_data_url, str) or not qr_data_url.startswith("data:image"):
            # Fallback: screenshot and crop
            location = qr_element.location
            size = qr_element.size
            padding = 20
            left = max(0, location['x'] - padding)
            top = max(0, location['y'] - padding)
            width = size['width'] + (padding * 2)
            height = size['height'] + (padding * 2)

            screenshot = driver.get_screenshot_as_png()
            image = Image.open(io.BytesIO(screenshot))
            qr_image = image.crop((left, top, left + width, top + height)).convert('RGB')
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
                    try:
                        _mark_device_connected(session_id)
                    except Exception as upd_err:
                        _safe_log(f"Failed to update device doc: {str(upd_err)}", "WhatsApp Device Update")
                    return
            except:
                pass
            
            # Check if QR expired or changed; if so, recapture and update session QR
            try:
                qr_element = driver.find_element(By.CSS_SELECTOR, '[data-ref] canvas')
                if not qr_element:
                    _safe_log("QR element missing, attempting recapture", "WhatsApp QR Monitor")
                    _recapture_qr(driver, session_id)
                    continue
                # If element exists but may have been refreshed, try to fetch fresh pixels periodically
                if int(time.time() - active_qr_sessions.get(session_id, {}).get('generated_at', 0)) >= 20:
                    _safe_log("Refreshing QR (periodic refresh)", "WhatsApp QR Monitor")
                    _recapture_qr(driver, session_id)
            except Exception as qr_check_err:
                _safe_log(f"QR check error, attempting recapture: {str(qr_check_err)}", "WhatsApp QR Monitor")
                _recapture_qr(driver, session_id)
            
            time.sleep(2)
            
    except Exception as e:
        _safe_log(f"QR Monitor Error: {str(e)}", "WhatsApp QR Monitor")

def _mark_device_connected(session_id):
    """Update WhatsApp Device doc matching session_id (number) to Connected"""
    try:
        # Find device by number field
        devices = frappe.get_all("WhatsApp Device", filters={"number": session_id}, fields=["name", "status"])
        if not devices:
            _safe_log(f"No WhatsApp Device found for number {session_id}", "WhatsApp Device Update")
            return
        name = devices[0]["name"]
        doc = frappe.get_doc("WhatsApp Device", name)
        doc.status = "Connected"
        # Clear QR code after connection if field exists
        try:
            if hasattr(doc, "qr_code"):
                doc.qr_code = None
        except Exception:
            pass
        doc.save()
        _safe_log(f"Device {name} marked Connected", "WhatsApp Device Update")
    except Exception as e:
        _safe_log(f"Device update failed: {str(e)}", "WhatsApp Device Update")

@frappe.whitelist()
def sync_device_status(session_id):
    """Manually sync device status from active QR session to WhatsApp Device"""
    try:
        session = active_qr_sessions.get(session_id)
        if not session:
            return {"status": "not_found"}
        if session.get("status") == "connected":
            _mark_device_connected(session_id)
            return {"status": "connected", "synced": True}
        return {"status": session.get("status"), "synced": False}
    except Exception as e:
        frappe.log_error(f"Sync device status failed: {str(e)}", "WhatsApp Device Sync")
        return {"status": "error", "message": str(e)}
def _recapture_qr(driver, session_id):
    """Recapture QR from current page and update session store"""
    try:
        # Wait for QR to be present again
        wait = WebDriverWait(driver, 15)
        qr_element = None
        qr_selectors = [
            '[data-ref] canvas',
            'canvas[aria-label*="QR"]',
            'div[data-ref] canvas',
            'canvas'
        ]
        for selector in qr_selectors:
            try:
                qr_element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                if qr_element and qr_element.size['width'] > 200 and qr_element.size['height'] > 200:
                    break
            except:
                continue

        if not qr_element:
            raise Exception("Unable to locate QR element on recapture")

        # Prefer canvas pixels; fallback to cropped screenshot
        qr_data_url = None
        try:
            qr_data_url = driver.execute_script("""
                const c = document.querySelector('[data-ref] canvas') || document.querySelector('canvas[aria-label*="QR"]') || document.querySelector('div[data-ref] canvas') || document.querySelector('canvas');
                if (!c) return null;
                try { return c.toDataURL('image/png'); } catch (e) { return null; }
            """)
        except Exception as js_err:
            _safe_log(f"Canvas toDataURL (recapture) failed: {str(js_err)}", "WhatsApp QR Recapture")

        if not qr_data_url or not isinstance(qr_data_url, str) or not qr_data_url.startswith("data:image"):
            location = qr_element.location
            size = qr_element.size
            padding = 20
            left = max(0, location['x'] - padding)
            top = max(0, location['y'] - padding)
            width = size['width'] + (padding * 2)
            height = size['height'] + (padding * 2)
            import io as _io
            import base64 as _b64
            from PIL import Image as _Image
            screenshot = driver.get_screenshot_as_png()
            image = _Image.open(_io.BytesIO(screenshot))
            qr_image = image.crop((left, top, left + width, top + height)).convert('RGB')
            buffer = _io.BytesIO()
            qr_image.save(buffer, format='PNG', quality=95)
            img_str = _b64.b64encode(buffer.getvalue()).decode()
            qr_data_url = f"data:image/png;base64,{img_str}"

        active_qr_sessions[session_id] = {
            'status': 'qr_ready',
            'qr_data': qr_data_url,
            'generated_at': time.time(),
            'driver_active': True
        }
        _safe_log("QR recaptured and session updated", "WhatsApp QR Recapture")
    except Exception as rec_err:
        _safe_log(f"QR recapture failed: {str(rec_err)}", "WhatsApp QR Recapture")

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
