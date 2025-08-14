#!/usr/bin/env python3
"""
Debug script to test Substack login process
"""

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from config import EMAIL, PASSWORD

def debug_login():
    """Debug the login process step by step"""

    print(f"🔐 Starting login debug with email: {EMAIL}")

    # Setup Chrome options
    options = ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Use the local chromedriver
    service = Service(executable_path='./chromedriver-mac-x64/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)

    try:
        # Step 1: Navigate to sign-in page
        print("📱 Step 1: Navigating to Substack sign-in page...")
        driver.get("https://substack.com/sign-in")
        print(f"   Current URL: {driver.current_url}")
        print(f"   Page title: {driver.title}")

        # Take a screenshot
        driver.save_screenshot("debug_step1_signin_page.png")
        print("   📸 Screenshot saved: debug_step1_signin_page.png")

        time.sleep(3)

        # Step 2: Look for sign-in options
        print("🔍 Step 2: Looking for sign-in options...")

        # Find all possible sign-in elements
        signin_options = driver.find_elements(By.CSS_SELECTOR, "a, button")
        print(f"   Found {len(signin_options)} clickable elements")

        # Look for specific login options
        password_options = driver.find_elements(By.XPATH, "//a[contains(@class, 'login-option')]")
        print(f"   Found {len(password_options)} elements with 'login-option' class")

        # Try different selectors
        selectors_to_try = [
            "//a[@class='login-option substack-login__login-option']",
            "//a[contains(@class, 'login-option')]",
            "//a[contains(text(), 'password')]",
            "//a[contains(text(), 'Continue with email')]",
            "//button[contains(text(), 'Continue with email')]"
        ]

        found_element = None
        for selector in selectors_to_try:
            elements = driver.find_elements(By.XPATH, selector)
            if elements:
                print(f"   ✅ Found element with selector: {selector}")
                print(f"      Element text: '{elements[0].text}'")
                found_element = elements[0]
                break
            else:
                print(f"   ❌ No elements found with selector: {selector}")

        if not found_element:
            print("   🚨 No sign-in button found! Let's check the page source...")
            with open("debug_page_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("   📄 Page source saved to: debug_page_source.html")
            return False

        # Step 3: Click the sign-in option
        print("👆 Step 3: Clicking sign-in option...")
        driver.save_screenshot("debug_step3_before_click.png")
        found_element.click()
        time.sleep(3)

        print(f"   Current URL after click: {driver.current_url}")
        driver.save_screenshot("debug_step3_after_click.png")

        # Step 4: Look for email and password fields
        print("📝 Step 4: Looking for email and password fields...")

        email_selectors = ["input[name='email']", "input[type='email']", "#email"]
        password_selectors = ["input[name='password']", "input[type='password']", "#password"]

        email_field = None
        password_field = None

        for selector in email_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                email_field = elements[0]
                print(f"   ✅ Found email field with selector: {selector}")
                break

        for selector in password_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                password_field = elements[0]
                print(f"   ✅ Found password field with selector: {selector}")
                break

        if not email_field or not password_field:
            print("   🚨 Could not find email or password fields!")
            with open("debug_login_page_source.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            print("   📄 Login page source saved to: debug_login_page_source.html")
            return False

        # Step 5: Fill in credentials
        print("✍️  Step 5: Filling in credentials...")
        email_field.clear()
        password_field.clear()
        email_field.send_keys(EMAIL)
        password_field.send_keys(PASSWORD)
        print("   Credentials entered")

        driver.save_screenshot("debug_step5_credentials_entered.png")

        # Step 6: Find and click submit button
        print("🚀 Step 6: Looking for submit button...")

        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:contains('Sign in')",
            "button:contains('Continue')",
            "form button",
            "//*[@id='substack-login']/div[2]/div[2]/form/button"
        ]

        submit_button = None
        for selector in submit_selectors:
            if selector.startswith("//"):
                elements = driver.find_elements(By.XPATH, selector)
            else:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)

            if elements:
                submit_button = elements[0]
                print(f"   ✅ Found submit button with selector: {selector}")
                print(f"      Button text: '{submit_button.text}'")
                break

        if not submit_button:
            print("   🚨 Could not find submit button!")
            return False

        # Click submit
        submit_button.click()
        print("   Submit button clicked")

        # Step 7: Wait and check result
        print("⏰ Step 7: Waiting for login result...")
        time.sleep(10)

        print(f"   Current URL after login attempt: {driver.current_url}")
        driver.save_screenshot("debug_step7_after_login.png")

        # Check for error messages
        error_containers = driver.find_elements(By.ID, 'error-container')
        if error_containers and error_containers[0].is_displayed():
            error_text = error_containers[0].text
            print(f"   ❌ Error found: {error_text}")
            return False

        # Check if we're still on sign-in page
        if 'sign-in' in driver.current_url:
            print("   ❌ Still on sign-in page - login likely failed")
            return False

        print("   ✅ Login appears successful!")
        return True

    except Exception as e:
        print(f"   🚨 Exception during login: {e}")
        driver.save_screenshot("debug_error.png")
        return False

    finally:
        print("🔚 Debug session complete. Browser will stay open for 30 seconds...")
        time.sleep(30)
        driver.quit()

if __name__ == "__main__":
    success = debug_login()
    if success:
        print("✅ Login debug successful!")
    else:
        print("❌ Login debug failed - check screenshots and HTML files")
