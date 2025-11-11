import frappe
import requests
import base64
import io
import hashlib
from PIL import Image
import time
import threading
import platform
import shutil
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import os
import urllib.parse

# Global storage for active QR sessions
active_qr_sessions = {}
# Global storage for active drivers (to keep sessions alive)
active_drivers = {}

@frappe.whitelist()
def generate_whatsapp_qr(session_id, timeout=30):
    """Generate QR code for WhatsApp Web authentication with better error handling"""
    try:
        frappe.log_error(f"Starting QR generation for session: {session_id}", "WhatsApp QR Debug")
        
        # Check if we already have an active session
        if session_id in active_qr_sessions:
            session_data = active_qr_sessions[session_id]
            status = session_data.get('status')
            
            if status == 'qr_ready':
                # Return latest QR code (may have been updated)
                return {
                    'status': 'qr_generated',
                    'qr': session_data.get('qr_data'),
                    'session': session_id,
                    'message': 'QR code ready for scanning',
                    'generated_at': session_data.get('generated_at')
                }
            elif status == 'connected':
                return {
                    'status': 'already_connected',
                    'session': session_id,
                    'message': 'WhatsApp is already connected',
                    'connected_at': session_data.get('connected_at')
                }
            elif status == 'starting':
                # Session is still starting, wait for it
                pass
            elif status == 'error':
                # Previous session had error, clear it and start fresh
                del active_qr_sessions[session_id]
        
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
        # If a background session exists, return its current status so client can poll
        if session_id in active_qr_sessions:
            session_data = active_qr_sessions.get(session_id, {})
            status = session_data.get('status', 'starting')
            payload = {
                'status': status,
                'session': session_id,
                'message': f'QR generation still in progress after {timeout} seconds'
            }
            if status == 'qr_ready' and session_data.get('qr_data'):
                payload['qr'] = session_data.get('qr_data')
            return payload
        raise Exception(f"QR generation timed out after {timeout} seconds")
        
    except Exception as e:
        error_msg = str(e)
        frappe.log_error(f"WhatsApp QR Generation Error: {error_msg}", "WhatsApp Real QR")
        # Clean up failed session from memory
        if session_id in active_qr_sessions:
            del active_qr_sessions[session_id]
        # Note: We don't delete the directory here as it might be useful for debugging
        # User can manually call cleanup_session if needed
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

def _build_chrome_options(user_data_dir=None):
    """Build Chrome options with all necessary arguments"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # Use new headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")
    chrome_options.add_argument("--disable-breakpad")
    chrome_options.add_argument("--disable-component-extensions-with-background-pages")
    chrome_options.add_argument("--disable-features=TranslateUI")
    chrome_options.add_argument("--disable-ipc-flooding-protection")
    chrome_options.add_argument("--disable-renderer-backgrounding")
    chrome_options.add_argument("--force-color-profile=srgb")
    chrome_options.add_argument("--metrics-recording-only")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--window-size=1280,720")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    # Improve compatibility with WhatsApp Web
    chrome_options.add_argument("--lang=en-US,en")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Add user data dir if provided
    if user_data_dir:
        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
    
    return chrome_options

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
        
        # Set up session directory with error handling and cleanup
        use_persistent_session = False
        effective_session_dir = None
        skip_persistent_due_to_lock = False
        
        if session_dir:
            try:
                # Ensure directory exists first
                os.makedirs(session_dir, exist_ok=True)
                
                # Clean up Chrome lock files (from previous crashed sessions)
                # Chrome creates lock files in multiple locations
                lock_files_to_check = [
                    os.path.join(session_dir, "SingletonLock"),
                    os.path.join(session_dir, "SingletonSocket"),
                    os.path.join(session_dir, "SingletonCookie"),
                ]
                
                # Also check in Default profile subdirectory (common on Windows)
                default_profile = os.path.join(session_dir, "Default")
                if os.path.exists(default_profile):
                    lock_files_to_check.extend([
                        os.path.join(default_profile, "SingletonLock"),
                        os.path.join(default_profile, "lockfile"),
                        os.path.join(default_profile, "LOCKFILE"),
                    ])
                
                # Clean up any existing lock files
                for lock_path in lock_files_to_check:
                    if os.path.exists(lock_path):
                        try:
                            # On Windows, files might be locked; try to remove with a small delay
                            if platform.system() == 'Windows':
                                time.sleep(0.1)
                            os.remove(lock_path)
                            _safe_log(f"Removed lock file: {lock_path}", "WhatsApp Session Cleanup")
                        except PermissionError:
                            # File is locked by another process - skip persistent session
                            _safe_log(f"Lock file is in use, will skip persistent session: {lock_path}", "WhatsApp Session Cleanup")
                            skip_persistent_due_to_lock = True
                            break
                        except Exception as lock_err:
                            _safe_log(f"Could not remove lock file {lock_path}: {str(lock_err)}", "WhatsApp Session Cleanup")
                            # Continue - might still work
                
                # Only proceed if we didn't hit a permission error and directory is writable
                if not skip_persistent_due_to_lock:
                    # Test if directory is writable
                    test_file = os.path.join(session_dir, ".test_write")
                    try:
                        with open(test_file, 'w') as f:
                            f.write("test")
                        os.remove(test_file)
                        effective_session_dir = session_dir
                        use_persistent_session = True
                        _safe_log(f"Session directory configured: {session_dir}", "WhatsApp Session Dir")
                    except Exception as write_err:
                        _safe_log(f"Session directory not writable: {str(write_err)}", "WhatsApp Session Dir Error")
                        effective_session_dir = None
                        use_persistent_session = False
                    
            except Exception as dir_error:
                _safe_log(f"Session directory error: {str(dir_error)}", "WhatsApp Session Dir Error")
                effective_session_dir = None
                use_persistent_session = False
        
        if not use_persistent_session:
            _safe_log("Running without persistent session directory", "WhatsApp Session Dir")
        
        # Create driver with enhanced error handling and retry logic
        max_retries = 2
        driver = None
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Build chrome options (with or without user-data-dir based on retry)
                # Use persistent session only on first attempt if it's enabled
                current_session_dir = effective_session_dir if (attempt == 0 and use_persistent_session) else None
                chrome_options = _build_chrome_options(user_data_dir=current_session_dir)
                
                # Install ChromeDriver
                chromedriver_path = ChromeDriverManager().install()
                _safe_log(f"ChromeDriver path: {chromedriver_path} (attempt {attempt + 1})", "WhatsApp ChromeDriver")
                
                # Create service
                service = ChromeService(chromedriver_path)
                
                # Try to create driver
                driver = webdriver.Chrome(service=service, options=chrome_options)
                driver.set_page_load_timeout(20)
                driver.implicitly_wait(5)
                _safe_log(f"Chrome driver created successfully (attempt {attempt + 1})", "WhatsApp Chrome Success")
                break
                
            except Exception as driver_error:
                error_str = str(driver_error)
                last_error = error_str
                _safe_log(f"Chrome driver creation failed (attempt {attempt + 1}): {error_str}", "WhatsApp Chrome Error")
                
                # If it's a session directory issue and we're using one, retry without it
                if attempt == 0 and use_persistent_session:
                    error_lower = error_str.lower()
                    if any(keyword in error_lower for keyword in ["user-data-dir", "profile", "lock", "session", "chrome instance exited"]):
                        _safe_log("Retrying without persistent session directory due to error", "WhatsApp Chrome Retry")
                        use_persistent_session = False
                        effective_session_dir = None
                        continue
                
                # If this is the last attempt, we'll raise the error below
                if attempt < max_retries - 1:
                    time.sleep(1)  # Wait before retry
        
        if driver is None:
            raise Exception(f"Failed to start Chrome after {max_retries} attempts: {last_error or 'Unknown error'}")
        
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
        
        # Extract QR code directly from canvas with better accuracy
        qr_data_url = _extract_qr_from_canvas(driver, qr_element)
        
        # Store driver reference to keep it alive
        active_drivers[session_id] = driver
        
        # Update session status
        active_qr_sessions[session_id] = {
            'status': 'qr_ready',
            'qr_data': qr_data_url,
            'generated_at': time.time(),
            'driver_active': True,
            'session_dir': effective_session_dir if use_persistent_session else None
        }
        
        _safe_log(f"QR capture successful for session: {session_id}", "WhatsApp QR Success")
        
        # Keep driver alive and monitor for QR changes and connection
        # Run monitor in a separate thread so it doesn't block
        monitor_thread = threading.Thread(target=monitor_qr_scan, args=(driver, session_id, effective_session_dir))
        monitor_thread.daemon = True
        monitor_thread.start()
        
    except Exception as e:
        error_msg = str(e)
        _safe_log(f"QR Capture Error for {session_id}: {error_msg}", "WhatsApp QR Capture")
        active_qr_sessions[session_id] = {
            'status': 'error',
            'error': error_msg
        }
        # Clean up driver on error
        if session_id in active_drivers:
            try:
                active_drivers[session_id].quit()
            except:
                pass
            del active_drivers[session_id]
        elif driver:
            try:
                driver.quit()
            except:
                pass

def _extract_qr_from_canvas(driver, qr_element=None):
    """Extract QR code directly from canvas with highest accuracy"""
    try:
        # Try to get QR from canvas using the most accurate method
        qr_data_url = driver.execute_script("""
            (function() {
                // First, try to find the exact QR canvas with data-ref attribute
                let canvas = document.querySelector('canvas[data-ref]');
                
                // If not found, try other selectors
                if (!canvas) {
                    canvas = document.querySelector('div[data-ref] canvas');
                }
                if (!canvas) {
                    canvas = document.querySelector('canvas[aria-label*="QR"]');
                }
                if (!canvas) {
                    // Last resort: find any canvas that might be QR
                    const canvases = document.querySelectorAll('canvas');
                    for (let c of canvases) {
                        const rect = c.getBoundingClientRect();
                        // QR codes are typically square and between 200-400px
                        if (rect.width >= 200 && rect.height >= 200 && 
                            Math.abs(rect.width - rect.height) < 50) {
                            canvas = c;
                            break;
                        }
                    }
                }
                
                if (!canvas) return null;
                
                try {
                    // Get canvas data as PNG with highest quality
                    return canvas.toDataURL('image/png');
                } catch (e) {
                    console.error('Canvas toDataURL error:', e);
                    return null;
                }
            })();
        """)
        
        if qr_data_url and isinstance(qr_data_url, str) and qr_data_url.startswith("data:image"):
            return qr_data_url
        
        # Fallback: screenshot method if canvas extraction fails
        if qr_element:
            try:
                location = qr_element.location
                size = qr_element.size
                padding = 10  # Less padding for more accuracy
                left = max(0, location['x'] - padding)
                top = max(0, location['y'] - padding)
                width = size['width'] + (padding * 2)
                height = size['height'] + (padding * 2)

                screenshot = driver.get_screenshot_as_png()
                image = Image.open(io.BytesIO(screenshot))
                qr_image = image.crop((left, top, left + width, top + height)).convert('RGB')
                buffer = io.BytesIO()
                qr_image.save(buffer, format='PNG', quality=100)  # Maximum quality
                img_str = base64.b64encode(buffer.getvalue()).decode()
                return f"data:image/png;base64,{img_str}"
            except Exception as screenshot_err:
                _safe_log(f"Screenshot fallback failed: {str(screenshot_err)}", "WhatsApp QR Extract")
        
        return None
    except Exception as e:
        _safe_log(f"QR extraction error: {str(e)}", "WhatsApp QR Extract")
        return None

def _keep_session_alive(driver, session_id, session_dir=None):
    """Keep WhatsApp session alive after connection"""
    try:
        _safe_log(f"Starting session keep-alive for: {session_id}", "WhatsApp Keep-Alive")
        
        # Keep checking connection status periodically
        check_interval = 30  # Check every 30 seconds
        last_check = time.time()
        
        while session_id in active_drivers and session_id in active_qr_sessions:
            try:
                current_time = time.time()
                
                # Check connection status periodically
                if current_time - last_check >= check_interval:
                    last_check = current_time
                    try:
                        # Verify still connected
                        chat_list = driver.find_elements(By.CSS_SELECTOR, '[data-testid="chat-list"]')
                        if not chat_list:
                            # Connection lost
                            _safe_log(f"Connection lost for session: {session_id}", "WhatsApp Keep-Alive")
                            active_qr_sessions[session_id] = {
                                'status': 'disconnected',
                                'message': 'WhatsApp connection lost',
                                'session_dir': session_dir
                            }
                            break
                        else:
                            # Still connected, update last check time
                            if active_qr_sessions[session_id].get('status') == 'connected':
                                # Update connection time
                                active_qr_sessions[session_id]['last_check'] = current_time
                    except Exception as check_err:
                        # Error checking connection - might be disconnected
                        _safe_log(f"Error checking connection: {str(check_err)}", "WhatsApp Keep-Alive")
                        # Don't break immediately - might be temporary
                
                # Sleep to avoid excessive CPU usage
                time.sleep(10)
                
            except Exception as e:
                _safe_log(f"Error in keep-alive loop: {str(e)}", "WhatsApp Keep-Alive")
                time.sleep(10)
        
        _safe_log(f"Keep-alive thread ended for session: {session_id}", "WhatsApp Keep-Alive")
        
    except Exception as e:
        _safe_log(f"Keep-alive error for session {session_id}: {str(e)}", "WhatsApp Keep-Alive")

def _get_qr_hash(qr_data_url):
    """Get a simple hash of QR data to detect changes"""
    if not qr_data_url:
        return None
    try:
        # Use first 1000 chars of base64 data as hash (enough to detect changes)
        data_part = qr_data_url[22:1022] if len(qr_data_url) > 1022 else qr_data_url[22:]
        return hashlib.md5(data_part.encode()).hexdigest()
    except:
        return None

def monitor_qr_scan(driver, session_id, session_dir=None, timeout=600):
    """Monitor for QR scan, connection, and QR code changes"""
    try:
        start_time = time.time()
        last_qr_hash = None
        qr_check_interval = 3  # Check QR every 3 seconds
        connection_check_interval = 2  # Check connection every 2 seconds
        last_qr_check = 0
        last_connection_check = 0
        
        _safe_log(f"Starting QR monitor for session: {session_id}", "WhatsApp QR Monitor")
        
        while time.time() - start_time < timeout:
            current_time = time.time()
            
            # Check for connection more frequently
            if current_time - last_connection_check >= connection_check_interval:
                last_connection_check = current_time
                try:
                    # Check if connected (multiple indicators)
                    chat_list = driver.find_elements(By.CSS_SELECTOR, '[data-testid="chat-list"]')
                    side_panel = driver.find_elements(By.CSS_SELECTOR, '[data-testid="sidebar"]')
                    pane_side = driver.find_elements(By.CSS_SELECTOR, '[data-testid="pane-side"]')
                    
                    if chat_list or side_panel or pane_side:
                        # Connected! Update session and keep driver alive
                        _safe_log(f"Connection detected for session: {session_id}", "WhatsApp Connection")
                        active_qr_sessions[session_id] = {
                            'status': 'connected',
                            'connected_at': time.time(),
                            'message': 'Successfully connected to WhatsApp',
                            'session_dir': session_dir,
                            'driver_active': True
                        }
                        # Don't quit driver - keep session alive
                        # Driver will be kept in active_drivers dict
                        # Start a thread to keep session alive and monitor connection
                        keep_alive_thread = threading.Thread(
                            target=_keep_session_alive, 
                            args=(driver, session_id, session_dir)
                        )
                        keep_alive_thread.daemon = True
                        keep_alive_thread.start()
                        return
                except Exception as conn_check_err:
                    # Not connected yet, continue monitoring
                    pass
            
            # Check for QR changes less frequently but regularly
            if current_time - last_qr_check >= qr_check_interval:
                last_qr_check = current_time
                try:
                    # Check if QR element exists and get current QR
                    qr_elements = driver.find_elements(By.CSS_SELECTOR, '[data-ref] canvas')
                    
                    if qr_elements and len(qr_elements) > 0:
                        # QR element exists, extract current QR
                        qr_element = qr_elements[0]
                        current_qr = _extract_qr_from_canvas(driver, qr_element)
                        
                        if current_qr:
                            # Check if QR has changed by comparing hash
                            current_hash = _get_qr_hash(current_qr)
                            
                            if current_hash and current_hash != last_qr_hash:
                                # QR has changed, update session
                                _safe_log(f"QR code updated for session: {session_id}", "WhatsApp QR Update")
                                active_qr_sessions[session_id] = {
                                    'status': 'qr_ready',
                                    'qr_data': current_qr,
                                    'generated_at': time.time(),
                                    'driver_active': True,
                                    'session_dir': session_dir
                                }
                                last_qr_hash = current_hash
                    else:
                        # QR element not found - might be connecting or expired
                        # Wait a bit and check again
                        time.sleep(1)
                        
                except Exception as qr_check_err:
                    # Error checking QR, log and continue
                    _safe_log(f"QR check error: {str(qr_check_err)}", "WhatsApp QR Monitor")
            
            # Sleep briefly to avoid excessive CPU usage
            time.sleep(0.5)
            
        # Timeout reached
        _safe_log(f"QR monitor timeout for session: {session_id}", "WhatsApp QR Monitor")
        active_qr_sessions[session_id] = {
            'status': 'timeout',
            'message': 'QR scan timeout - please try again'
        }
            
    except Exception as e:
        _safe_log(f"QR Monitor Error: {str(e)}", "WhatsApp QR Monitor")
        active_qr_sessions[session_id] = {
            'status': 'error',
            'error': str(e)
        }


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
    """Check status of QR generation and return latest QR if available"""
    if session_id in active_qr_sessions:
        session_data = active_qr_sessions[session_id].copy()
        if session_data.get('status') == 'qr_ready' and session_id in active_drivers:
            try:
                driver = active_drivers[session_id]
                qr_elements = driver.find_elements(By.CSS_SELECTOR, '[data-ref] canvas')
                if qr_elements and len(qr_elements) > 0:
                    latest_qr = _extract_qr_from_canvas(driver, qr_elements[0])
                    if latest_qr:
                        current_hash = _get_qr_hash(latest_qr)
                        old_hash = _get_qr_hash(session_data.get('qr_data'))
                        if current_hash != old_hash:
                            session_data['qr_data'] = latest_qr
                            session_data['generated_at'] = time.time()
                            active_qr_sessions[session_id] = session_data
            except Exception:
                pass
        return session_data
    # Try to bootstrap status from persisted profile if not found in memory
    boot = _bootstrap_session_status(session_id)
    if boot:
        return boot
    return {'status': 'not_found'}

def _bootstrap_session_status(session_id):
    """Attempt to determine session status by starting Chrome with persisted profile."""
    try:
        session_dir = get_session_directory(session_id)
        chrome_options = _build_chrome_options(user_data_dir=session_dir)
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get("https://web.whatsapp.com")
        time.sleep(2)
        try:
            candidates = driver.find_elements(By.CSS_SELECTOR, "[data-testid='chat-list'], [data-testid='sidebar'], [data-testid='pane-side']")
            if candidates and len(candidates) > 0:
                active_drivers[session_id] = driver
                active_qr_sessions[session_id] = {
                    'status': 'connected',
                    'connected_at': time.time(),
                    'driver_active': True,
                    'session_dir': session_dir,
                }
                # Start keep-alive
                try:
                    t = threading.Thread(target=_keep_session_alive, args=(driver, session_id, session_dir))
                    t.daemon = True
                    t.start()
                except Exception:
                    pass
                return active_qr_sessions[session_id].copy()
        except Exception:
            pass
        # Not connected; check QR element
        try:
            qr_el = None
            selectors = ['[data-ref] canvas', 'canvas[aria-label*="QR"]', 'div[data-ref] canvas', 'canvas']
            for sel in selectors:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els and len(els) > 0:
                    for el in els:
                        size = el.size
                        if size.get('width', 0) > 200 and size.get('height', 0) > 200:
                            qr_el = el
                            break
                if qr_el:
                    break
            if qr_el:
                qr_data_url = _extract_qr_from_canvas(driver, qr_el)
                active_drivers[session_id] = driver
                active_qr_sessions[session_id] = {
                    'status': 'qr_ready',
                    'qr_data': qr_data_url,
                    'generated_at': time.time(),
                    'driver_active': True,
                    'session_dir': session_dir,
                }
                # Start monitor so that when user scans, status flips to connected
                try:
                    t = threading.Thread(target=monitor_qr_scan, args=(driver, session_id, session_dir))
                    t.daemon = True
                    t.start()
                except Exception:
                    pass
                return active_qr_sessions[session_id].copy()
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass
        return {'status': 'not_found'}
    except Exception as e:
        _safe_log(f"Bootstrap status failed for {session_id}: {str(e)}", "WhatsApp QR Bootstrap")
        return {'status': 'error', 'message': str(e)}

def _get_session_directory_path(session_id):
    """Get session directory path without creating it"""
    try:
        private_files = frappe.get_site_path('private', 'files')
        session_dir = os.path.join(private_files, 'whatsapp_sessions', session_id)
        return session_dir
    except Exception:
        # Fallback: we can't determine the path, return None
        return None

def _delete_session_directory(session_id, retry_count=3):
    """Delete session directory and all its contents"""
    try:
        # Get directory path without creating it
        session_dir = _get_session_directory_path(session_id)
        
        if not session_dir:
            _safe_log(f"Could not determine session directory path for {session_id}", "WhatsApp Session Cleanup")
            return False
        
        # Check if directory exists
        if not os.path.exists(session_dir):
            return True  # Directory doesn't exist, consider it deleted
        
        # First, try to clean up lock files
        lock_files_to_check = [
            os.path.join(session_dir, "SingletonLock"),
            os.path.join(session_dir, "SingletonSocket"),
            os.path.join(session_dir, "SingletonCookie"),
        ]
        
        default_profile = os.path.join(session_dir, "Default")
        if os.path.exists(default_profile):
            lock_files_to_check.extend([
                os.path.join(default_profile, "SingletonLock"),
                os.path.join(default_profile, "lockfile"),
                os.path.join(default_profile, "LOCKFILE"),
            ])
        
        # Remove lock files
        for lock_path in lock_files_to_check:
            if os.path.exists(lock_path):
                try:
                    if platform.system() == 'Windows':
                        time.sleep(0.2)  # Wait longer on Windows
                    os.remove(lock_path)
                except Exception:
                    pass  # Ignore lock file removal errors
        
        # Try to delete directory with retries
        for attempt in range(retry_count):
            try:
                if platform.system() == 'Windows':
                    # On Windows, use shutil.rmtree with error handler
                    def handle_remove_readonly(func, path, exc):
                        """Handle readonly files on Windows"""
                        try:
                            os.chmod(path, 0o777)
                            func(path)
                        except Exception:
                            pass
                    
                    shutil.rmtree(session_dir, onerror=handle_remove_readonly)
                else:
                    shutil.rmtree(session_dir)
                
                _safe_log(f"Session directory deleted: {session_dir}", "WhatsApp Session Cleanup")
                return True
                
            except PermissionError:
                if attempt < retry_count - 1:
                    time.sleep(0.5)  # Wait before retry
                    continue
                else:
                    _safe_log(f"Could not delete session directory (locked): {session_dir}", "WhatsApp Session Cleanup")
                    return False
            except Exception as e:
                _safe_log(f"Error deleting session directory: {str(e)}", "WhatsApp Session Cleanup")
                if attempt < retry_count - 1:
                    time.sleep(0.5)
                    continue
                return False
        
        return False
        
    except Exception as e:
        _safe_log(f"Error in _delete_session_directory: {str(e)}", "WhatsApp Session Cleanup")
        return False

@frappe.whitelist()
def cleanup_session(session_id, delete_directory=True, close_driver=True):
    """Clean up QR session from memory, close driver, and optionally delete session directory"""
    try:
        driver_closed = False
        
        # Close driver if requested and exists
        if close_driver and session_id in active_drivers:
            try:
                driver = active_drivers[session_id]
                driver.quit()
                driver_closed = True
                _safe_log(f"Driver closed for session: {session_id}", "WhatsApp Session Cleanup")
            except Exception as driver_err:
                _safe_log(f"Error closing driver for session {session_id}: {str(driver_err)}", "WhatsApp Session Cleanup")
            finally:
                del active_drivers[session_id]
        
        # Remove from active sessions
        if session_id in active_qr_sessions:
            del active_qr_sessions[session_id]
            _safe_log(f"Session removed from memory: {session_id}", "WhatsApp Session Cleanup")
        
        # Delete session directory if requested
        directory_deleted = False
        if delete_directory:
            deleted = _delete_session_directory(session_id)
            directory_deleted = deleted
            if deleted:
                _safe_log(f"Session directory deleted for: {session_id}", "WhatsApp Session Cleanup")
            else:
                _safe_log(f"Could not delete session directory for: {session_id}", "WhatsApp Session Cleanup")
        
        return {
            'success': True,
            'message': f'Session {session_id} cleaned up successfully',
            'driver_closed': driver_closed,
            'directory_deleted': directory_deleted
        }
            
    except Exception as e:
        error_msg = str(e)
        _safe_log(f"Error cleaning up session {session_id}: {error_msg}", "WhatsApp Session Cleanup")
        return {
            'success': False,
            'error': error_msg
        }

@frappe.whitelist()
def cleanup_old_sessions(older_than_days=7, delete_directories=True):
    """Clean up old sessions that are no longer active"""
    try:
        cleaned_count = 0
        deleted_dirs = 0
        errors = []
        
        # Get all session directories
        try:
            private_files = frappe.get_site_path('private', 'files')
            sessions_base_dir = os.path.join(private_files, 'whatsapp_sessions')
            
            if not os.path.exists(sessions_base_dir):
                return {
                    'success': True,
                    'message': 'No sessions directory found',
                    'cleaned_count': 0
                }
            
            # Get current time
            current_time = time.time()
            cutoff_time = current_time - (older_than_days * 24 * 60 * 60)
            
            # Iterate through session directories
            for session_id in os.listdir(sessions_base_dir):
                session_dir = os.path.join(sessions_base_dir, session_id)
                
                if not os.path.isdir(session_dir):
                    continue
                
                # Check if session is old
                try:
                    dir_mtime = os.path.getmtime(session_dir)
                    if dir_mtime < cutoff_time:
                        # Session is old, clean it up
                        if session_id in active_qr_sessions:
                            del active_qr_sessions[session_id]
                            cleaned_count += 1
                        
                        if delete_directories:
                            if _delete_session_directory(session_id):
                                deleted_dirs += 1
                            else:
                                errors.append(f"Could not delete directory for session {session_id}")
                except Exception as e:
                    errors.append(f"Error processing session {session_id}: {str(e)}")
            
            return {
                'success': True,
                'cleaned_count': cleaned_count,
                'deleted_directories': deleted_dirs,
                'errors': errors if errors else None,
                'message': f'Cleaned up {cleaned_count} old sessions, deleted {deleted_dirs} directories'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
            
    except Exception as e:
        _safe_log(f"Error in cleanup_old_sessions: {str(e)}", "WhatsApp Session Cleanup")
        return {
            'success': False,
            'error': str(e)
        }

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

@frappe.whitelist()
def send_message_persistent(session_id, phone_number, message):
    """Send a WhatsApp message using the persistent Selenium driver.

    Requires that session_id exists in active_drivers (i.e., device linked and kept alive).
    """
    try:
        # Ensure we have a live driver for this session (workers may differ per request)
        if session_id not in active_drivers:
            ensured = _ensure_driver_for_session(session_id)
            if not ensured:
                return {
                    'success': False,
                    'error': 'Session not connected'
                }

        driver = active_drivers[session_id]

        # Normalize phone to digits only
        dest = ''.join(filter(str.isdigit, phone_number or ''))
        if not dest:
            return {
                'success': False,
                'error': 'Invalid destination number'
            }

        text = urllib.parse.quote(message or '')
        chat_url = f"https://web.whatsapp.com/send?phone={dest}&text={text}"
        driver.get(chat_url)

        wait = WebDriverWait(driver, 15)
        send_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='send']"))
        )
        send_button.click()

        return {
            'success': True,
            'message_id': f"{session_id}_{dest}_{int(time.time())}",
            'timestamp': frappe.utils.now()
        }
    except Exception as e:
        frappe.log_error(f"Persistent send error for {session_id}: {str(e)}", "WhatsApp Persistent Send")
        return {
            'success': False,
            'error': str(e)
        }

def _ensure_driver_for_session(session_id):
    """Ensure a Chrome driver is running and logged-in for the given session_id.

    This handles cases where the QR was generated in a different worker/process and
    active_drivers is empty in this process. If the persisted Chrome profile exists
    and is logged in, we can reattach by starting a new headless driver with the
    same user-data-dir and verifying the chat list.
    """
    try:
        if session_id in active_drivers:
            return True

        # Build Chrome with the persisted user data dir
        try:
            session_dir = get_session_directory(session_id)
        except Exception:
            session_dir = None

        chrome_options = _build_chrome_options(user_data_dir=session_dir)
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)

        try:
            driver.get("https://web.whatsapp.com")
            # small settle time
            time.sleep(2)
            # Check if connected by locating chat list/sidebar
            wait = WebDriverWait(driver, 10)
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='chat-list'], [data-testid='sidebar'], [data-testid='pane-side']")))

            # Mark connected in session map
            active_drivers[session_id] = driver
            active_qr_sessions[session_id] = {
                'status': 'connected',
                'connected_at': time.time(),
                'driver_active': True,
                'session_dir': session_dir,
            }

            # Kick off keep-alive in background
            try:
                keep_alive_thread = threading.Thread(target=_keep_session_alive, args=(driver, session_id, session_dir))
                keep_alive_thread.daemon = True
                keep_alive_thread.start()
            except Exception:
                pass

            return True
        except Exception as e:
            # Not connected with this profile
            try:
                driver.quit()
            except Exception:
                pass
            return False

    except Exception as e:
        _safe_log(f"Ensure driver failed for {session_id}: {str(e)}", "WhatsApp Driver Ensure")
        return False
