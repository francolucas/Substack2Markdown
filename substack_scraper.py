import argparse
import json
import os
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from time import sleep

from bs4 import BeautifulSoup
import html2text
import markdown
import requests
from tqdm import tqdm
from xml.etree import ElementTree as ET

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from urllib.parse import urlparse
from config import EMAIL, PASSWORD

USE_PREMIUM: bool = False  # Set to True if you want to login to Substack and convert paid for posts
BASE_SUBSTACK_URL: str = "https://www.thefitzwilliam.com/"  # Substack you want to convert to markdown
BASE_MD_DIR: str = "substack_md_files"  # Name of the directory we'll save the .md essay files
HTML_TEMPLATE: str = "author_template.html"  # HTML template to use for the author page
NUM_POSTS_TO_SCRAPE: int = 3  # Set to 0 if you want all posts


def extract_main_part(url: str) -> str:
    """
    Extract the appropriate name from URL for folder naming:
    - For substack.com domains: use subdomain (e.g., garymarcus.substack.com -> garymarcus)
    - For other domains: use main domain name, ignoring www (e.g., newsletter.eng-leadership.com -> eng-leadership)
    """
    parts = urlparse(url).netloc.split('.')

    # Check if it's a substack domain
    if len(parts) >= 2 and parts[-2] == 'substack' and parts[-1] == 'com':
        return parts[0]

    # For other domains, get the main domain name
    if len(parts) >= 2:
        # Remove www prefix if present
        if parts[0] == 'www':
            parts = parts[1:]

        # For most cases, the main domain is the first remaining part
        # unless it's a known multi-part TLD like co.uk, com.au, etc.
        if len(parts) >= 3 and parts[-2] in ['co', 'com', 'net', 'org', 'gov', 'edu']:
            # Handle cases like example.co.uk -> example
            return parts[-3]
        elif len(parts) >= 2:
            # Handle cases like subdomain.domain.com -> domain
            return parts[-2]

    return parts[0] if parts else 'unknown'


def generate_html_file(author_name: str, output_dir: str) -> None:
    """
    Generates an index.html file for the given author in the specified publication directory.
    """
    # Read JSON data from the publication directory
    json_path = os.path.join(output_dir, f'{author_name}.json')
    with open(json_path, 'r', encoding='utf-8') as file:
        essays_data = json.load(file)

    # Convert JSON data to a JSON string for embedding
    embedded_json_data = json.dumps(essays_data, ensure_ascii=False, indent=4)

    with open(HTML_TEMPLATE, 'r', encoding='utf-8') as file:
        html_template = file.read()

    # Update asset paths to be relative to the publication directory
    # Since index.html is in the publication root, assets are in ./assets/
    html_template = html_template.replace('../assets/', 'assets/')

    # Insert the JSON string into the script tag in the HTML template
    html_with_data = html_template.replace('<!-- AUTHOR_NAME -->', author_name).replace(
        '<script type="application/json" id="essaysData"></script>',
        f'<script type="application/json" id="essaysData">{embedded_json_data}</script>'
    )
    html_with_author = html_with_data.replace('author_name', author_name)

    # Write the modified HTML to index.html in the publication directory
    html_output_path = os.path.join(output_dir, 'index.html')
    with open(html_output_path, 'w', encoding='utf-8') as file:
        file.write(html_with_author)

    print(f"Generated index.html: {html_output_path}")


class BaseSubstackScraper(ABC):
    def __init__(self, base_substack_url: str, md_save_dir: str = None, html_save_dir: str = None):
        if not base_substack_url.endswith("/"):
            base_substack_url += "/"
        self.base_substack_url: str = base_substack_url

        self.writer_name: str = extract_main_part(base_substack_url)

        # Fixed directory structure: substacks/writer_name/
        self.base_output_dir: str = f"substacks/{self.writer_name}"
        self.md_save_dir: str = f"{self.base_output_dir}/markdown"
        self.html_save_dir: str = f"{self.base_output_dir}/html"

        # Create directories
        if not os.path.exists(self.base_output_dir):
            os.makedirs(self.base_output_dir)
            print(f"Created publication directory {self.base_output_dir}")

        if not os.path.exists(self.md_save_dir):
            os.makedirs(self.md_save_dir)
            print(f"Created markdown directory {self.md_save_dir}")

        if not os.path.exists(self.html_save_dir):
            os.makedirs(self.html_save_dir)
            print(f"Created html directory {self.html_save_dir}")

        # Copy assets to publication directory
        self.copy_assets_to_output()

        self.keywords: List[str] = ["about", "archive", "podcast"]
        self.post_urls: List[str] = self.get_all_post_urls()

    def copy_assets_to_output(self) -> None:
        """Copy assets folder to the publication directory"""
        import shutil

        source_assets = "./assets"
        target_assets = os.path.join(self.base_output_dir, "assets")

        if os.path.exists(source_assets):
            if os.path.exists(target_assets):
                shutil.rmtree(target_assets)  # Remove existing assets
            shutil.copytree(source_assets, target_assets)
            print(f"Copied assets to {target_assets}")
        else:
            print(f"Warning: Assets folder not found at {source_assets}")

    def get_all_post_urls(self) -> List[str]:
        """
        Attempts to fetch URLs from sitemap.xml, falling back to feed.xml if necessary.
        """
        urls = self.fetch_urls_from_sitemap()
        if not urls:
            urls = self.fetch_urls_from_feed()
        return self.filter_urls(urls, self.keywords)

    def fetch_urls_from_sitemap(self) -> List[str]:
        """
        Fetches URLs from sitemap.xml.
        """
        sitemap_url = f"{self.base_substack_url}sitemap.xml"
        response = requests.get(sitemap_url)

        if not response.ok:
            print(f'Error fetching sitemap at {sitemap_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = [element.text for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        return urls

    def fetch_urls_from_feed(self) -> List[str]:
        """
        Fetches URLs from feed.xml.
        """
        print('Falling back to feed.xml. This will only contain up to the 22 most recent posts.')
        feed_url = f"{self.base_substack_url}feed.xml"
        response = requests.get(feed_url)

        if not response.ok:
            print(f'Error fetching feed at {feed_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = []
        for item in root.findall('.//item'):
            link = item.find('link')
            if link is not None and link.text:
                urls.append(link.text)

        return urls

    @staticmethod
    def filter_urls(urls: List[str], keywords: List[str]) -> List[str]:
        """
        This method filters out URLs that contain certain keywords
        """
        return [url for url in urls if all(keyword not in url for keyword in keywords)]

    @staticmethod
    def html_to_md(html_content: str) -> str:
        """
        This method converts HTML to Markdown
        """
        if not isinstance(html_content, str):
            raise ValueError("html_content must be a string")
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return h.handle(html_content)

    @staticmethod
    def save_to_file(filepath: str, content: str) -> None:
        """
        This method saves content to a file. Can be used to save HTML or Markdown
        """
        if not isinstance(filepath, str):
            raise ValueError("filepath must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        if os.path.exists(filepath):
            print(f"File already exists: {filepath}")
            return

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(content)

    @staticmethod
    def md_to_html(md_content: str) -> str:
        """
        This method converts Markdown to HTML
        """
        return markdown.markdown(md_content, extensions=['extra'])


    def save_to_html_file(self, filepath: str, content: str) -> None:
        """
        This method saves HTML content to a file with a link to an external CSS file.
        """
        if not isinstance(filepath, str):
            raise ValueError("filepath must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        # Calculate the relative path from the HTML file to the CSS file
        # HTML files are now in publication/html/ so we need to go up one level to reach assets
        html_dir = os.path.dirname(filepath)
        assets_dir = os.path.join(self.base_output_dir, "assets")
        css_path = os.path.relpath(os.path.join(assets_dir, "css", "essay-styles.css"), html_dir)
        css_path = css_path.replace("\\", "/")  # Ensure forward slashes for web paths

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Markdown Content</title>
    <link rel="stylesheet" href="{css_path}">
</head>
<body>
    <main class="markdown-content">
{content}
    </main>
</body>
</html>"""

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(html_content)

    @staticmethod
    def get_filename_from_url(url: str, filetype: str = ".md") -> str:
        """
        Gets the filename from the URL (the ending)
        """
        if not isinstance(url, str):
            raise ValueError("url must be a string")

        if not isinstance(filetype, str):
            raise ValueError("filetype must be a string")

        if not filetype.startswith("."):
            filetype = f".{filetype}"

        return url.split("/")[-1] + filetype

    @staticmethod
    def combine_metadata_and_content(title: str, subtitle: str, date: str, like_count: str, content) -> str:
        """
        Combines the title, subtitle, and content into a single string with Markdown format
        """
        if not isinstance(title, str):
            raise ValueError("title must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        metadata = f"# {title}\n\n"
        if subtitle:
            metadata += f"## {subtitle}\n\n"
        metadata += f"**{date}**\n\n"
        metadata += f"**Likes:** {like_count}\n\n"

        return metadata + content

    def extract_post_data(self, soup: BeautifulSoup) -> Tuple[str, str, str, str, str]:
        """
        Converts substack post soup to markdown, returns metadata and content
        """
        title = soup.select_one("h1.post-title, h2").text.strip()  # When a video is present, the title is demoted to h2

        subtitle_element = soup.select_one("h3.subtitle")
        subtitle = subtitle_element.text.strip() if subtitle_element else ""


        date_element = soup.find(
            "div",
            class_="pencraft pc-reset color-pub-secondary-text-hGQ02T line-height-20-t4M0El font-meta-MWBumP size-11-NuY2Zx weight-medium-fw81nC transform-uppercase-yKDgcq reset-IxiVJZ meta-EgzBVA"
        )
        date = date_element.text.strip() if date_element else "Date not found"

        like_count_element = soup.select_one("a.post-ufi-button .label")
        like_count = (
            like_count_element.text.strip()
            if like_count_element and like_count_element.text.strip().isdigit()
            else "0"
        )

        content = str(soup.select_one("div.available-content"))
        md = self.html_to_md(content)
        md_content = self.combine_metadata_and_content(title, subtitle, date, like_count, md)
        return title, subtitle, like_count, date, md_content

    @abstractmethod
    def get_url_soup(self, url: str) -> str:
        raise NotImplementedError

    def save_essays_data_to_json(self, essays_data: list) -> None:
        """
        Saves essays data to a JSON file for a specific author in the publication directory.
        """
        json_path = os.path.join(self.base_output_dir, f'{self.writer_name}.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as file:
                existing_data = json.load(file)
            essays_data = existing_data + [data for data in essays_data if data not in existing_data]
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(essays_data, f, ensure_ascii=False, indent=4)

        print(f"Saved essays data to {json_path}")

    def scrape_posts(self, num_posts_to_scrape: int = 0) -> None:
        """
        Iterates over all posts and saves them as markdown and html files
        """
        essays_data = []
        count = 0
        total = num_posts_to_scrape if num_posts_to_scrape != 0 else len(self.post_urls)
        for url in tqdm(self.post_urls, total=total):
            try:
                md_filename = self.get_filename_from_url(url, filetype=".md")
                html_filename = self.get_filename_from_url(url, filetype=".html")
                md_filepath = os.path.join(self.md_save_dir, md_filename)
                html_filepath = os.path.join(self.html_save_dir, html_filename)

                if not os.path.exists(md_filepath):
                    soup = self.get_url_soup(url)
                    if soup is None:
                        total += 1
                        continue
                    title, subtitle, like_count, date, md = self.extract_post_data(soup)
                    self.save_to_file(md_filepath, md)

                    # Convert markdown to HTML and save
                    html_content = self.md_to_html(md)
                    self.save_to_html_file(html_filepath, html_content)

                    essays_data.append({
                        "title": title,
                        "subtitle": subtitle,
                        "like_count": like_count,
                        "date": date,
                        "file_link": os.path.relpath(md_filepath, self.base_output_dir),
                        "html_link": os.path.relpath(html_filepath, self.base_output_dir)
                    })
                else:
                    print(f"File already exists: {md_filepath}")
            except Exception as e:
                print(f"Error scraping post: {e}")
            count += 1
            if num_posts_to_scrape != 0 and count == num_posts_to_scrape:
                break
        self.save_essays_data_to_json(essays_data=essays_data)
        generate_html_file(author_name=self.writer_name, output_dir=self.base_output_dir)


class SubstackScraper(BaseSubstackScraper):
    def __init__(self, base_substack_url: str):
        super().__init__(base_substack_url)

    def get_url_soup(self, url: str) -> Optional[BeautifulSoup]:
        """
        Gets soup from URL using requests
        """
        try:
            page = requests.get(url, headers=None)
            soup = BeautifulSoup(page.content, "html.parser")
            if soup.find("h2", class_="paywall-title"):
                print(f"Skipping premium article: {url}")
                return None
            return soup
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


class PremiumSubstackScraper(BaseSubstackScraper):
    def __init__(
            self,
            base_substack_url: str,
            headless: bool = False,
            chrome_path: str = '',
            chrome_driver_path: str = '',
            user_agent: str = ''
    ) -> None:
        super().__init__(base_substack_url)

        options = ChromeOptions()
        if headless:
            options.add_argument("--headless")
        if chrome_path:
            options.binary_location = chrome_path
        if user_agent:
            options.add_argument(f'user-agent={user_agent}')  # Pass this if running headless and blocked by captcha

        # Add options to ensure better content loading
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        # Set page load timeout
        options.add_argument("--page-load-strategy=normal")

        if chrome_driver_path:
            service = Service(executable_path=chrome_driver_path)
        else:
            service = Service(ChromeDriverManager().install())

        self.driver = webdriver.Chrome(service=service, options=options)

        # Set timeouts for better reliability
        self.driver.set_page_load_timeout(15)  # 15 seconds for page load
        self.driver.implicitly_wait(2)  # 2 seconds for element finding

        self.login()

    def login(self) -> None:
        """
        This method logs into Substack using Selenium with manual CAPTCHA support
        """
        print("🔐 Starting Substack login process...")
        self.driver.get("https://substack.com/sign-in")
        wait = WebDriverWait(self.driver, 15)

        try:
            print("📱 Looking for sign-in with password option...")
            # Wait for and click the sign-in with password option
            signin_with_password = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//a[@class='login-option substack-login__login-option']"))
            )
            signin_with_password.click()
            print("✅ Clicked sign-in with password")

            print("📝 Looking for email and password fields...")
            # Wait for email and password fields to be present
            email_field = wait.until(EC.presence_of_element_located((By.NAME, "email")))
            password_field = wait.until(EC.presence_of_element_located((By.NAME, "password")))

            # Clear fields and enter credentials
            email_field.clear()
            password_field.clear()
            email_field.send_keys(EMAIL)
            password_field.send_keys(PASSWORD)
            print(f"✅ Entered credentials for: {EMAIL}")

            # Wait for and click submit button
            submit_button = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//*[@id='substack-login']/div[2]/div[2]/form/button"))
            )
            submit_button.click()
            print("✅ Clicked submit button")

            # MANUAL CAPTCHA INTERVENTION
            print("\n🤖 CAPTCHA DETECTED!")
            print("🔍 Please check the browser window and solve any CAPTCHA if present.")
            print("⏰ Waiting 120 seconds for manual intervention...")
            print("   - Solve the CAPTCHA in the browser")
            print("   - Complete the login process manually if needed")
            print("   - The script will continue automatically")

            # Wait longer for manual CAPTCHA solving
            for i in range(120, 0, -10):
                print(f"   ⏳ {i} seconds remaining...")
                sleep(10)

                # Check if login succeeded during the wait
                if 'substack.com/sign-in' not in self.driver.current_url:
                    print("   ✅ Login appears successful! Continuing...")
                    return  # Exit the method successfully

                # Check for error messages but don't break immediately
                error_containers = self.driver.find_elements(By.ID, 'error-container')
                if error_containers and error_containers[0].is_displayed():
                    error_text = error_containers[0].text
                    if "captcha" in error_text.lower():
                        print(f"   🔄 CAPTCHA still pending: {error_text}")
                        continue  # Keep waiting
                    else:
                        print(f"   ❌ Error detected: {error_text}")
                        break

        except Exception as e:
            print(f"❌ Login process encountered an error: {e}")
            print("🔍 Check the browser window for issues")
            sleep(10)  # Give time to see what's happening

        # Final check
        if self.is_login_failed():
            print("❌ Login unsuccessful after manual intervention")
            raise ValueError(
                "Login failed. Possible issues:\n"
                "1. CAPTCHA not solved correctly\n"
                "2. Invalid credentials\n"
                "3. Account locked or suspended\n"
                "4. Substack login page structure changed\n"
                "Check the browser window for error messages."
            )
        else:
            print("✅ Login successful!")

    def is_login_failed(self) -> bool:
        """
        Check for login failure indicators
        """
        # Check for error containers
        error_containers = self.driver.find_elements(By.ID, 'error-container')
        if error_containers and error_containers[0].is_displayed():
            error_text = error_containers[0].text
            print(f"🚨 Login error detected: {error_text}")
            return True

        # Check if still on sign-in page
        current_url = self.driver.current_url
        if 'substack.com/sign-in' in current_url:
            print(f"🚨 Still on sign-in page: {current_url}")
            return True

        # Check for other error indicators
        error_messages = self.driver.find_elements(By.CSS_SELECTOR, ".error, .alert-error, [role='alert']")
        for error in error_messages:
            if error.is_displayed() and error.text.strip():
                print(f"🚨 Error message found: {error.text}")
                return True

        print(f"✅ Login check passed. Current URL: {current_url}")
        return False

    def get_url_soup(self, url: str) -> BeautifulSoup:
        """
        Gets soup from URL using logged in selenium driver with optimized wait for content loading
        """
        try:
            self.driver.get(url)
            wait = WebDriverWait(self.driver, 5, 0.5)  # 5 seconds max, poll every 0.5 seconds

            # Wait for the main content to load
            # Look for key elements that indicate the article has loaded
            try:
                # Wait for either the article content or a paywall indicator
                wait.until(
                    lambda driver:
                        driver.find_elements(By.CSS_SELECTOR, "div.available-content") or
                        driver.find_elements(By.CSS_SELECTOR, "h1.post-title") or
                        driver.find_elements(By.CSS_SELECTOR, "h2.paywall-title") or
                        driver.find_elements(By.CSS_SELECTOR, ".post-content")
                )

                # Check if content is still loading (look for loading indicators)
                loading_indicators = self.driver.find_elements(By.CSS_SELECTOR,
                    ".loading, .spinner, [data-testid='loading'], .skeleton")
                if loading_indicators:
                    sleep(1)  # Reduced from 3 to 1 second

            except Exception as e:
                print(f"Timeout waiting for content to load on {url}: {e}")

            return BeautifulSoup(self.driver.page_source, "html.parser")

        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape a Substack site.")
    parser.add_argument(
        "-u", "--url", type=str, help="The base URL of the Substack site to scrape."
    )
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=0,
        help="The number of posts to scrape. If 0 or not provided, all posts will be scraped.",
    )
    parser.add_argument(
        "-p",
        "--premium",
        action="store_true",
        help="Include -p in command to use the Premium Substack Scraper with selenium.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Include -h in command to run browser in headless mode when using the Premium Substack "
        "Scraper.",
    )
    parser.add_argument(
        "--chrome-path",
        type=str,
        default="",
        help='Optional: The path to the Chrome browser executable (i.e. "path_to_chrome.exe").',
    )
    parser.add_argument(
        "--chrome-driver-path",
        type=str,
        default="",
        help='Optional: The path to the Chrome WebDriver executable (i.e. "path_to_chromedriver.exe").',
    )
    parser.add_argument(
        "--user-agent",
        type=str,
        default="",
        help="Optional: Specify a custom user agent for selenium browser automation. Useful for "
        "passing captcha in headless mode",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.url:
        if args.premium:
            scraper = PremiumSubstackScraper(
                args.url,
                headless=args.headless,
                chrome_path=args.chrome_path,
                chrome_driver_path=args.chrome_driver_path,
                user_agent=args.user_agent
            )
        else:
            scraper = SubstackScraper(args.url)
        scraper.scrape_posts(args.number)

    else:  # Use the hardcoded values at the top of the file
        if USE_PREMIUM:
            scraper = PremiumSubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                chrome_path=args.chrome_path,
                chrome_driver_path=args.chrome_driver_path
            )
        else:
            scraper = SubstackScraper(BASE_SUBSTACK_URL)
        scraper.scrape_posts(num_posts_to_scrape=NUM_POSTS_TO_SCRAPE)


if __name__ == "__main__":
    main()
