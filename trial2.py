# Required libraries: playwright, beautifulsoup4
# Installation in Colab:
# !pip install playwright beautifulsoup4
# !playwright install

import json
import time
import asyncio # Required for async operations and sleep
import logging
import os
import traceback # For detailed error printing

# Import the async version of Playwright
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from bs4 import BeautifulSoup

# --- Logging Setup ---
# Configure logging to provide informational messages during execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__) # Use __name__ for logger identification

# --- Helper Functions ---

def save_html(html_content: str, filename: str = "debug_page.html"):
    """
    Saves the provided HTML content to a specified file for debugging purposes.
    This function remains synchronous as it only performs file I/O.

    Args:
        html_content: The HTML string to save.
        filename: The name of the file to save the HTML to.
    """
    try:
        # Ensure the directory exists if the filename includes a path (optional)
        # os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"Successfully saved HTML content to {filename}")
    except IOError as e:
        logger.error(f"IOError saving HTML to {filename}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error saving HTML to {filename}: {e}")

async def load_cookies_playwright(context, cookies_source: str) -> bool:
    """
    Loads cookies from a JSON file or a JSON string into the Playwright BrowserContext.
    Filters cookies for the '.linkedin.com' domain and formats them correctly.
    Validates the 'sameSite' attribute.
    Uses async context.add_cookies.

    Args:
        context: The Playwright BrowserContext object.
        cookies_source: Path to the cookies JSON file or a JSON string.

    Returns:
        True if cookies were loaded successfully, False otherwise.
    """
    logger.info(f"Attempting to load cookies from: {cookies_source[:70]}...") # Log source start
    try:
        cookies_list = []
        # Check if the source is likely a JSON string
        if cookies_source.strip().startswith('[') and cookies_source.strip().endswith(']'):
            try:
                cookies_list = json.loads(cookies_source)
                logger.info("Parsed cookies directly from JSON string.")
            except json.JSONDecodeError:
                logger.warning("Input looks like JSON string but failed to parse, assuming file path.")
                # Fallback to treating as file path if JSON string parse fails
                try:
                    with open(cookies_source, 'r', encoding='utf-8') as f:
                        cookies_list = json.load(f)
                    logger.info(f"Loaded cookies from file: {cookies_source}")
                except FileNotFoundError:
                     logger.error(f"Cookie file not found: {cookies_source}")
                     return False
                except json.JSONDecodeError:
                     logger.error(f"Error decoding JSON from cookie file: {cookies_source}")
                     return False

        # Else, assume it's a file path
        else:
            try:
                with open(cookies_source, 'r', encoding='utf-8') as f:
                    cookies_list = json.load(f)
                logger.info(f"Loaded cookies from file: {cookies_source}")
            except FileNotFoundError:
                logger.error(f"Cookie file not found: {cookies_source}")
                return False
            except json.JSONDecodeError:
                logger.error(f"Error decoding JSON from cookie file: {cookies_source}")
                return False
            except Exception as e:
                logger.error(f"Unexpected error opening or reading cookie file {cookies_source}: {e}")
                return False

        # --- Format cookies for Playwright ---
        formatted_cookies = []
        required_keys = {'name', 'value', 'domain'}
        valid_same_site_values = {'Strict', 'Lax', 'None'}

        for cookie in cookies_list:
            if not isinstance(cookie, dict):
                logger.warning(f"Skipping non-dictionary item in cookies list: {cookie}")
                continue
            if not required_keys.issubset(cookie.keys()):
                logger.warning(f"Skipping cookie missing required fields (name/value/domain): {cookie.get('name', 'N/A')}")
                continue
            if '.linkedin.com' not in cookie.get('domain', ''):
                logger.debug(f"Skipping cookie for non-LinkedIn domain {cookie.get('domain')}: {cookie.get('name')}")
                continue

            expires_timestamp = -1
            expiry_val = cookie.get('expirationDate', cookie.get('expiry'))
            if expiry_val is not None:
                try:
                    expires_timestamp = int(float(expiry_val))
                except (ValueError, TypeError):
                    logger.warning(f"Could not parse expiry '{expiry_val}' for cookie '{cookie.get('name')}'. Treating as session cookie.")

            input_same_site = cookie.get('sameSite')
            final_same_site = 'Lax'
            if input_same_site is None:
                pass
            elif input_same_site in valid_same_site_values:
                final_same_site = input_same_site
            else:
                logger.warning(f"Cookie '{cookie.get('name')}': Invalid sameSite value '{input_same_site}' found. Defaulting to 'Lax'.")

            formatted_cookie = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie['domain'],
                'path': cookie.get('path', '/'),
                'expires': expires_timestamp,
                'httpOnly': cookie.get('httpOnly', False),
                'secure': cookie.get('secure', True),
                'sameSite': final_same_site
            }
            formatted_cookies.append(formatted_cookie)

        if not formatted_cookies:
             logger.warning(f"No suitable cookies found for LinkedIn domains in the provided source.")
             return False

        logger.info(f"Adding {len(formatted_cookies)} formatted cookies to the browser context.")
        await context.add_cookies(formatted_cookies)
        logger.info("Cookies successfully added to the browser context.")
        return True

    except Exception as e:
        logger.error(f"An unexpected error occurred in load_cookies_playwright: {e}")
        traceback.print_exc()
        return False

async def check_login_status_playwright(page: Page) -> bool:
    """
    Checks if the session is logged into LinkedIn by navigating to the feed,
    handling an intermediate "Sign in as" prompt if present,
    and looking for a specific element that indicates a logged-in state.
    Uses async page methods.

    Args:
        page: The Playwright Page object.

    Returns:
        True if logged in, False otherwise.
    """
    logger.info("Checking LinkedIn login status...")
    feed_url = "https://www.linkedin.com/feed/"
    login_indicator_selector = "div.share-box-feed-entry__closed-share-box"
    # ***** IMPORTANT: Inspect the actual button on LinkedIn to confirm the best selector. *****
    sign_in_as_button_selector = 'button:has-text("LinkedIn User")'
    # sign_in_as_button_selector = 'button[aria-label*="Sign in as"]' # Alternative

    try:
        logger.info(f"Navigating to LinkedIn feed: {feed_url}")
        await page.goto(feed_url, wait_until='domcontentloaded', timeout=60000)
        await asyncio.sleep(5)

        logger.info(f"Checking for intermediate 'Sign in as' button ('{sign_in_as_button_selector}')...")
        try:
            sign_in_button = page.locator(sign_in_as_button_selector)
            await sign_in_button.wait_for(state='visible', timeout=10000)
            logger.info("Intermediate 'Sign in as' button found. Clicking it...")
            await sign_in_button.click()
            logger.info("Clicked the 'Sign in as' button. Waiting for transition...")
            await asyncio.sleep(7)
        except PlaywrightTimeoutError:
            logger.info("Intermediate 'Sign in as' button not found within timeout (proceeding as normal).")
        except PlaywrightError as e:
            logger.warning(f"Playwright error checking/clicking intermediate button: {e}")

        current_url = page.url
        logger.info(f"Current URL after navigation/potential intermediate step: {current_url}")

        if any(sub in current_url for sub in ["/login", "/authwall", "/challenge", "/checkpoint"]):
            logger.error(f"Redirected to login/authwall/challenge page: {current_url}")
            save_html(await page.content(), "playwright_redirect_page.html")
            return False

        logger.info(f"Looking for final login indicator element with selector: '{login_indicator_selector}'")
        try:
            await page.locator(login_indicator_selector).wait_for(state='visible', timeout=30000)
            logger.info("Login indicator found on feed page. Login appears successful.")
            save_html(await page.content(), "playwright_feed_page_success.html")
            return True
        except PlaywrightTimeoutError:
            logger.error(f"Timeout: Could not find final login indicator ('{login_indicator_selector}') on feed page.")
            save_html(await page.content(), "playwright_feed_page_fail_indicator_not_found.html")
            return False
        except PlaywrightError as e:
             logger.error(f"Playwright error while looking for final login indicator: {e}")
             save_html(await page.content(), "playwright_feed_page_error_locator.html")
             return False

    except PlaywrightTimeoutError:
        logger.error(f"Timeout occurred during navigation to {feed_url} or initial checks.")
        try:
            save_html(await page.content(), "playwright_navigation_timeout_page.html")
        except Exception as save_err:
             logger.error(f"Could not save HTML after navigation timeout: {save_err}")
        return False
    except PlaywrightError as e:
        logger.error(f"A Playwright error occurred checking login status: {e}")
        try:
            save_html(await page.content(), "playwright_general_error_page.html")
        except Exception as save_err:
             logger.error(f"Could not save HTML after Playwright error: {save_err}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred checking login status: {e}")
        traceback.print_exc()
        try:
            save_html(await page.content(), "playwright_unexpected_error_page.html")
        except Exception as save_err:
             logger.error(f"Could not save HTML after unexpected error: {save_err}")
        return False

# --- Main Execution Logic ---

async def main_playwright():
    """
    Main asynchronous function to launch Playwright, load cookies, check login,
    and scrape a target profile if logged in using updated selectors.
    """
    logger.info("Starting Playwright script...")
    async with async_playwright() as p:
        browser = None
        context = None
        page = None
        scraped_data = {} # Dictionary to hold scraped data

        try:
            logger.info("Launching Chromium browser...")
            browser = await p.chromium.launch(headless=False, args=[ # Set headless=True for background running
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ])
            logger.info("Browser launched successfully.")

            logger.info("Creating new browser context.")
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                # viewport={'width': 1280, 'height': 800}, # Optional: Set viewport
                # locale='en-US' # Optional: Set locale
            )
            logger.info("Browser context created.")

            page = await context.new_page()
            logger.info("New page created.")

            cookies_file_path = 'cookies.json'
            if not os.path.exists(cookies_file_path):
                 logger.error(f"CRITICAL: Cookie file '{cookies_file_path}' not found. Please upload it.")
                 raise FileNotFoundError(f"Cookie file '{cookies_file_path}' not found.")

            if not await load_cookies_playwright(context, cookies_file_path):
                logger.error(f"Failed to load cookies from {cookies_file_path}. Exiting.")
                raise Exception("Cookie loading failed")

            is_logged_in = await check_login_status_playwright(page)

            if is_logged_in:
                logger.info("LinkedIn session confirmed. Proceeding to profile scraping.")

                # --- Define Target Profile ---
                # profile_url = "https://www.linkedin.com/in/williamhgates/" # Example
                profile_url = "https://www.linkedin.com/in/kuldeep-paul/" # Using the example name provided
                logger.info(f"Attempting to access profile: {profile_url}")

                try:
                    await page.goto(profile_url, wait_until='domcontentloaded', timeout=60000)
                    # Increased sleep time to allow dynamic content like posts to load
                    logger.info("Waiting for page elements to render...")
                    await asyncio.sleep(10) # Adjust sleep time as needed

                    # --- Define NEW Selectors based on provided HTML ---
                    profile_name_selector = "h1.break-words"
                    headline_selector = "div.text-body-medium.break-words" # Kept the previous selector as it matches
                    location_selector = "span.text-body-small.t-black--light.break-words"
                    post_container_selector = 'div[data-urn^="urn:li:activity:"]' # Selector for post containers

                    # --- Verify Profile Page Loaded (Using Name as Indicator) ---
                    logger.info(f"Looking for profile name element ('{profile_name_selector}')...")
                    await page.locator(profile_name_selector).first.wait_for(state='visible', timeout=30000)
                    logger.info("Profile page main content indicator (Name) loaded.")

                    # --- Extract Data ---
                    logger.info("Extracting data from profile page...")
                    page_html = await page.content() # Get HTML after waiting
                    save_html(page_html, "playwright_target_profile_page.html")

                    # 1. Extract Name
                    try:
                        name_element = page.locator(profile_name_selector).first
                        name = await name_element.text_content(timeout=5000)
                        scraped_data['name'] = name.strip() if name else None
                        logger.info(f"Name: {scraped_data['name']}")
                    except Exception as e:
                        logger.warning(f"Could not extract Name: {e}")
                        scraped_data['name'] = None

                    # 2. Extract Bio (Headline)
                    try:
                        headline_element = page.locator(headline_selector).first
                        headline = await headline_element.text_content(timeout=5000)
                        scraped_data['headline'] = headline.strip() if headline else None
                        logger.info(f"Headline: {scraped_data['headline']}")
                    except Exception as e:
                        logger.warning(f"Could not extract Headline: {e}")
                        scraped_data['headline'] = None

                    # 3. Extract Location
                    try:
                        # Location might not be the first match, let's try finding the one near the headline
                        # This assumes location is structurally close to headline, adjust if needed
                        # A more robust way might involve finding a parent container first.
                        location_element = page.locator(location_selector).first # Check if .first works, otherwise more specific logic needed
                        location = await location_element.text_content(timeout=5000)
                        scraped_data['location'] = location.strip() if location else None
                        logger.info(f"Location: {scraped_data['location']}")
                    except Exception as e:
                        logger.warning(f"Could not extract Location using .first: {e}")
                        scraped_data['location'] = None
                        # Potential Fallback (if needed): Iterate through matches if .first isn't right
                        # all_locations = await page.locator(location_selector).all()
                        # for loc_element in all_locations:
                        #     loc_text = await loc_element.text_content()
                        #     # Add logic here to check if loc_text looks like a location
                        #     logger.info(f"Potential location found: {loc_text.strip()}")


                    # 4. Extract Post URLs
                    scraped_data['post_urls'] = []
                    try:
                        logger.info("Extracting post URLs...")
                        post_locators = await page.locator(post_container_selector).all()
                        logger.info(f"Found {len(post_locators)} potential post elements.")

                        if not post_locators:
                             logger.info("No elements found matching the post selector. Check if the 'Activity' section is loaded/visible.")

                        for i, post_locator in enumerate(post_locators):
                            try:
                                urn = await post_locator.get_attribute('data-urn')
                                if urn and urn.startswith('urn:li:activity:'):
                                    post_url = f"https://www.linkedin.com/feed/update/{urn}/"
                                    scraped_data['post_urls'].append(post_url)
                                    logger.debug(f"Extracted post URN {i+1}: {urn} -> URL: {post_url}")
                                else:
                                     logger.warning(f"Element {i+1} matched selector but missing valid data-urn.")
                            except Exception as e_inner:
                                logger.warning(f"Error extracting data-urn from post element {i+1}: {e_inner}")

                        logger.info(f"Extracted {len(scraped_data['post_urls'])} post URLs.")
                        if scraped_data['post_urls']:
                           logger.info(f"Sample Post URL: {scraped_data['post_urls'][0]}")


                    except Exception as e:
                        logger.error(f"Could not extract Post URLs: {e}")


                    # --- Log Final Scraped Data ---
                    logger.info("--- Final Scraped Data ---")
                    logger.info(json.dumps(scraped_data, indent=2))
                    logger.info("--------------------------")


                # --- Error Handling for Profile Scraping ---
                except PlaywrightTimeoutError:
                    logger.error(f"Timeout occurred loading/finding elements on profile page: {profile_url}")
                    if page:
                        try: save_html(await page.content(), "playwright_profile_timeout_page.html")
                        except Exception as save_err: logger.error(f"Could not save HTML after profile timeout: {save_err}")
                except PlaywrightError as e:
                    logger.error(f"Playwright error accessing profile page {profile_url}: {e}")
                    if page:
                        try: save_html(await page.content(), "playwright_profile_playwright_error_page.html")
                        except Exception as save_err: logger.error(f"Could not save HTML after profile Playwright error: {save_err}")
                except Exception as e:
                    logger.error(f"Unexpected error accessing/scraping profile page {profile_url}: {e}")
                    traceback.print_exc()
                    if page:
                        try: save_html(await page.content(), "playwright_profile_unexpected_error_page.html")
                        except Exception as save_err: logger.error(f"Could not save HTML after profile unexpected error: {save_err}")
            else:
                logger.error("LinkedIn session check failed. Cannot proceed to profile scraping.")

        # --- General Error Handling ---
        except FileNotFoundError as e:
             logger.error(f"Setup error: {e}")
        except Exception as e:
            logger.error(f"An error occurred in the main execution block: {e}")
            traceback.print_exc()
            if page:
                 try: save_html(await page.content(), "playwright_main_exception_page.html")
                 except Exception as save_err: logger.error(f"Could not save HTML after main exception: {save_err}")

        # --- Cleanup ---
        finally:
            logger.info("Starting cleanup...")
            if page and not page.is_closed():
                try: await page.close(); logger.info("Page closed.")
                except Exception as e: logger.error(f"Error closing page: {e}")
            if context:
                try: await context.close(); logger.info("Browser context closed.")
                except Exception as e: logger.error(f"Error closing browser context: {e}")
            if browser:
                try: await browser.close(); logger.info("Browser closed.")
                except Exception as e: logger.error(f"Error closing browser: {e}")
            logger.info("Playwright script finished.")

# --- How to Run ---
# (Instructions remain the same as before)
# 1. Install dependencies: !pip install playwright beautifulsoup4 && playwright install
# 2. Upload 'cookies.json'.
# 3. Paste code into Colab cell.
# 4. Run in a new cell: await main_playwright()

# --- Standard Python script execution ---
if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        logger.info("Async event loop already running. Scheduling task.")
        async def run_main_in_existing_loop():
            await main_playwright()
        loop.create_task(run_main_in_existing_loop())
    else:
        logger.info("Starting new async event loop for main_playwright.")
        asyncio.run(main_playwright())