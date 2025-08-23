import frappe
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
import tempfile

@frappe.whitelist()
def generate_quick_qr(session_id):
    """Generate QR code quickly without session persistence"""
    try:
        # Set up Chrome options (no session persistence)
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1280,720")
        chrome_options.add_argument("--incognito")  # Use incognito mode
        
        # Create driver
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(15)
        
        try:
            # Navigate to WhatsApp Web
            driver.get("https://web.whatsapp.com")
            
            # Wait longer for page to fully load and stabilize
            time.sleep(10)  # Increased from 5 to 10 seconds
            
            # Look for QR code with longer timeout
            wait = WebDriverWait(driver, 25)  # Increased from 15 to 25 seconds
            
            # Try different QR selectors
            qr_selectors = [
                '[data-ref] canvas',
                'canvas[aria-label*="QR"]', 
                'div[data-ref] canvas',
                'canvas'
            ]
            
            qr_element = None
            for selector in qr_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for element in elements:
                        size = element.size
                        if size['width'] > 200 and size['height'] > 200:
                            qr_element = element
                            break
                    if qr_element:
                        break
                except:
                    continue
            
            if not qr_element:
                raise Exception("QR code element not found")
            
            # Wait a bit more for QR to stabilize
            time.sleep(3)  # Additional wait after finding QR
            
            # Take screenshot of QR area
            location = qr_element.location
            size = qr_element.size
            
            # Screenshot the QR area
            screenshot = driver.get_screenshot_as_png()
            image = Image.open(io.BytesIO(screenshot))
            
            # Crop QR area with padding
            padding = 30
            left = max(0, location['x'] - padding)
            top = max(0, location['y'] - padding) 
            right = min(image.width, location['x'] + size['width'] + padding)
            bottom = min(image.height, location['y'] + size['height'] + padding)
            
            qr_image = image.crop((left, top, right, bottom))
            
            # Convert to base64
            buffer = io.BytesIO()
            qr_image.save(buffer, format='PNG', quality=95)
            img_str = base64.b64encode(buffer.getvalue()).decode()
            qr_data_url = f"data:image/png;base64,{img_str}"
            
            return {
                'status': 'qr_generated',
                'qr': qr_data_url,
                'session': session_id,
                'message': 'Real WhatsApp QR generated successfully!'
            }
            
        finally:
            driver.quit()
            
    except Exception as e:
        frappe.log_error(f"Quick QR Error: {str(e)}", "WhatsApp Quick QR")
        return {
            'status': 'error',
            'message': str(e)
        }

@frappe.whitelist()
def health_check_quick():
    """Health check for quick QR service"""
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        
        service = ChromeService(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.get("https://web.whatsapp.com")
        driver.quit()
        
        return {
            'status': 'Quick QR Service Ready',
            'chrome_available': True,
            'version': '4.0.0 - Quick QR',
            'timestamp': frappe.utils.now()
        }
    except Exception as e:
        return {
            'status': 'Quick QR Service Failed',
            'chrome_available': False,
            'error': str(e),
            'timestamp': frappe.utils.now()
        }
