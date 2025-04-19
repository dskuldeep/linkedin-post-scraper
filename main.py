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
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
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
        # Playwright expects 'expires' as a Unix timestamp (seconds since epoch).
        # Cookie extensions might export 'expirationDate' or 'expiry'.
        formatted_cookies = []
        required_keys = {'name', 'value', 'domain'}
        # Define valid SameSite values according to Playwright's requirements
        valid_same_site_values = {'Strict', 'Lax', 'None'}

        for cookie in cookies_list:
            # Basic validation and domain filtering
            if not isinstance(cookie, dict):
                logger.warning(f"Skipping non-dictionary item in cookies list: {cookie}")
                continue
            if not required_keys.issubset(cookie.keys()):
                logger.warning(f"Skipping cookie missing required fields (name/value/domain): {cookie.get('name', 'N/A')}")
                continue
            if '.linkedin.com' not in cookie.get('domain', ''):
                logger.debug(f"Skipping cookie for non-LinkedIn domain {cookie.get('domain')}: {cookie.get('name')}")
                continue

            # Convert expiry date to Unix timestamp (integer seconds)
            expires_timestamp = -1 # Playwright's default for session cookies
            expiry_val = cookie.get('expirationDate', cookie.get('expiry'))
            if expiry_val is not None:
                try:
                    # Ensure it's treated as a number (float first for flexibility) then int
                    expires_timestamp = int(float(expiry_val))
                except (ValueError, TypeError):
                    logger.warning(f"Could not parse expiry '{expiry_val}' for cookie '{cookie.get('name')}'. Treating as session cookie.")

            # --- Validate SameSite attribute ---
            input_same_site = cookie.get('sameSite')
            final_same_site = 'Lax' # Default to Lax

            if input_same_site is None:
                # If sameSite key is missing, default is fine ('Lax')
                # logger.debug(f"Cookie '{cookie.get('name')}': sameSite missing, defaulting to 'Lax'.")
                pass # Already defaulted to Lax
            elif input_same_site in valid_same_site_values:
                 # If value is present and valid, use it
                final_same_site = input_same_site
            else:
                # If value is present but invalid (e.g., empty string, other value)
                logger.warning(f"Cookie '{cookie.get('name')}': Invalid sameSite value '{input_same_site}' found in cookies source. Defaulting to 'Lax'.")
                # Keep the default 'Lax'

            # Build the cookie dictionary in the format Playwright expects
            formatted_cookie = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie['domain'],
                'path': cookie.get('path', '/'), # Default path to '/'
                'expires': expires_timestamp,
                'httpOnly': cookie.get('httpOnly', False), # Default httpOnly to False
                'secure': cookie.get('secure', True),     # Default secure to True (HTTPS)
                'sameSite': final_same_site # Use the validated or defaulted value
            }
            formatted_cookies.append(formatted_cookie)

        if not formatted_cookies:
             logger.warning(f"No suitable cookies found for LinkedIn domains in the provided source.")
             return False

        # --- Add cookies to the context (async operation) ---
        logger.info(f"Adding {len(formatted_cookies)} formatted cookies to the browser context.")
        await context.add_cookies(formatted_cookies)
        logger.info("Cookies successfully added to the browser context.")
        return True

    except Exception as e:
        # Catch any other unexpected errors during the process
        logger.error(f"An unexpected error occurred in load_cookies_playwright: {e}")
        traceback.print_exc() # Print detailed traceback for debugging
        return False

async def check_login_status_playwright(page) -> bool:
    """
    Checks if the session is logged into LinkedIn by navigating to the feed
    and looking for a specific element that indicates a logged-in state.
    Uses async page methods.

    Args:
        page: The Playwright Page object.

    Returns:
        True if logged in, False otherwise.
    """
    logger.info("Checking LinkedIn login status...")
    feed_url = "https://www.linkedin.com/feed/"
    # Selectors for login indicators (these might change if LinkedIn updates its site)
    # Option 1: The main feed sharing composer box
    login_indicator_selector = "div.share-box-feed-entry__closed-share-box"
    # Option 2: The profile picture in the top navigation bar
    # login_indicator_selector = "img.global-nav__me-photo"
    # Option 3: The "Messaging" link in the top navigation
    # login_indicator_selector = "a[href*='/messaging/']"

    try:
        logger.info(f"Navigating to LinkedIn feed: {feed_url}")
        # Navigate to the feed page, wait for DOM content to be loaded
        # Increased timeout for potentially slow network/Colab environment
        await page.goto(feed_url, wait_until='domcontentloaded', timeout=60000) # 60 seconds timeout

        # Add a small delay to allow dynamic elements to potentially render after DOM load
        await asyncio.sleep(5) # 5 seconds sleep

        current_url = page.url
        logger.info(f"Current URL after navigation and sleep: {current_url}")

        # --- Check for redirects to login, authwall, or challenge pages ---
        if any(sub in current_url for sub in ["/login", "/authwall", "/challenge", "/checkpoint"]):
            logger.error(f"Redirected to login/authwall/challenge page: {current_url}")
            # Save the HTML of the redirect page for debugging
            save_html(await page.content(), "playwright_redirect_page.html")
            return False

        # --- Look for the login indicator element ---
        logger.info(f"Looking for login indicator element with selector: '{login_indicator_selector}'")
        try:
            # Wait for the chosen indicator element to be present and visible on the page
            # Increased timeout for element visibility
            await page.locator(login_indicator_selector).wait_for(state='visible', timeout=30000) # 30 seconds timeout

            # If wait_for completes without error, the element was found
            logger.info("Login indicator found on feed page. Login appears successful.")
            save_html(await page.content(), "playwright_feed_page_success.html")
            return True
        except PlaywrightTimeoutError:
            # If the element is not found within the timeout period
            logger.error(f"Timeout: Could not find login indicator ('{login_indicator_selector}') on feed page.")
            logger.info("Saving HTML of feed page for debugging (login indicator not found).")
            save_html(await page.content(), "playwright_feed_page_fail_indicator_not_found.html")
            return False
        except PlaywrightError as e:
             # Handle other potential Playwright errors during element location
             logger.error(f"Playwright error while looking for login indicator: {e}")
             save_html(await page.content(), "playwright_feed_page_error_locator.html")
             return False

    except PlaywrightTimeoutError:
        # Handle timeouts during the initial page navigation
        logger.error(f"Timeout occurred during navigation to {feed_url}.")
        try:
            save_html(await page.content(), "playwright_navigation_timeout_page.html")
        except Exception as save_err:
             logger.error(f"Could not save HTML after navigation timeout: {save_err}")
        return False
    except PlaywrightError as e:
        # Handle other potential Playwright errors during navigation/page interaction
        logger.error(f"A Playwright error occurred checking login status: {e}")
        try:
            save_html(await page.content(), "playwright_general_error_page.html")
        except Exception as save_err:
             logger.error(f"Could not save HTML after Playwright error: {save_err}")
        return False
    except Exception as e:
        # Catch any other unexpected errors
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
    and scrape a target profile if logged in.
    """
    logger.info("Starting Playwright script...")
    # Use the async context manager for Playwright
    async with async_playwright() as p:
        browser = None # Initialize browser variable
        context = None # Initialize context variable
        try:
            # --- Launch Browser ---
            logger.info("Launching Chromium browser...")
            # headless=False shows the browser window (useful for debugging, not possible in basic Colab)
            # headless=True runs in the background (standard for automation)
            browser = await p.chromium.launch(headless=False, args=[
                '--no-sandbox',                 # Required for running as root/in containers like Colab
                '--disable-dev-shm-usage',      # Overcomes limited shared memory resources
                '--disable-gpu',                # Often needed in headless environments
                # '--disable-blink-features=AutomationControlled' # Experimental: Might help avoid bot detection
            ])
            logger.info("Browser launched successfully.")

            # --- Create Browser Context ---
            # A browser context is like an isolated browser profile
            logger.info("Creating new browser context.")
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36', # Mimic a real browser
                # You can configure other context options here (viewport, locale, etc.)
                # viewport={'width': 1280, 'height': 800},
                # locale='en-US'
            )
            logger.info("Browser context created.")

            # --- Create Page ---
            page = await context.new_page()
            logger.info("New page created.")

            # --- Load Cookies ---
            # Ensure cookies.json is present in the Colab environment's root directory
            # or provide the correct path.
            cookies_file_path = 'cookies.json'
            if not await load_cookies_playwright(context, cookies_file_path):
                logger.error(f"Failed to load cookies from {cookies_file_path}. Exiting.")
                # No need to return here, finally block will handle cleanup
                raise Exception("Cookie loading failed") # Raise exception to go to finally block

            # --- Check Login Status ---
            is_logged_in = await check_login_status_playwright(page)

            if is_logged_in:
                logger.info("LinkedIn session confirmed via Playwright. Proceeding to profile scraping.")

                # --- Navigate to Target Profile ---
                # Replace with the actual profile URL you want to scrape
                profile_url = "https://www.linkedin.com/in/williamhgates/" # Example: Bill Gates
                # profile_url = "https://www.linkedin.com/in/alexwang2911/" # Example: Alex Wang
                logger.info(f"Attempting to access profile: {profile_url}")

                try:
                    # Navigate to the profile page
                    await page.goto(profile_url, wait_until='domcontentloaded', timeout=60000) # 60s timeout
                    await asyncio.sleep(5) # Allow time for dynamic content rendering

                    # --- Verify Profile Page Loaded ---
                    # Wait for a specific element unique to profile pages to ensure loading
                    # Example: The main heading containing the person's name
                    # This selector might change based on LinkedIn updates. Inspect the page to confirm.
                    profile_name_selector = "h1.text-heading-xlarge"
                    logger.info(f"Looking for profile name element ('{profile_name_selector}')...")
                    await page.locator(profile_name_selector).wait_for(state='visible', timeout=30000) # 30s timeout
                    logger.info("Profile page main content indicator loaded.")

                    # --- Extract Data ---
                    logger.info("Extracting data from profile page...")
                    page_html = await page.content()
                    save_html(page_html, "playwright_target_profile_page.html") # Save profile HTML

                    # Option 1: Use Playwright locators (often simpler and more robust)
                    name = await page.locator(profile_name_selector).text_content()
                    logger.info(f"Profile Name (Playwright): {name.strip()}")

                    # Example: Extract headline (selector might need adjustment)
                    headline_selector = "div.text-body-medium.break-words"
                    try:
                         # Use .first in case there are multiple matches (e.g., in "About" section)
                        headline = await page.locator(headline_selector).first.text_content(timeout=5000) # Shorter timeout ok
                        logger.info(f"Headline (Playwright): {headline.strip()}")
                    except PlaywrightTimeoutError:
                        logger.warning("Could not find headline element using Playwright locator.")
                    except Exception as e:
                         logger.warning(f"Error extracting headline with Playwright: {e}")


                    # Option 2: Use BeautifulSoup to parse the saved HTML (if preferred)
                    soup = BeautifulSoup(page_html, 'html.parser')
                    title_tag = soup.find('title')
                    if title_tag:
                        logger.info(f"Profile Page Title (BS4): {title_tag.text.strip()}")
                    # Extract name using BS4 (example, adapt selector if needed)
                    name_element_bs4 = soup.select_one(profile_name_selector)
                    if name_element_bs4:
                         logger.info(f"Profile Name (BS4): {name_element_bs4.get_text(strip=True)}")
                    else:
                         logger.warning("Could not find name element using BS4 selector.")

                    # Add more data extraction logic here as needed...

                # --- Error Handling for Profile Scraping ---
                except PlaywrightTimeoutError:
                    logger.error(f"Timeout occurred while loading or finding elements on profile page: {profile_url}")
                    try:
                         save_html(await page.content(), "playwright_profile_timeout_page.html")
                    except Exception as save_err:
                         logger.error(f"Could not save HTML after profile timeout: {save_err}")
                except PlaywrightError as e:
                    logger.error(f"A Playwright error occurred accessing profile page {profile_url}: {e}")
                    try:
                        save_html(await page.content(), "playwright_profile_playwright_error_page.html")
                    except Exception as save_err:
                         logger.error(f"Could not save HTML after profile Playwright error: {save_err}")
                except Exception as e:
                    logger.error(f"An unexpected error occurred accessing profile page {profile_url}: {e}")
                    traceback.print_exc()
                    try:
                        save_html(await page.content(), "playwright_profile_unexpected_error_page.html")
                    except Exception as save_err:
                         logger.error(f"Could not save HTML after profile unexpected error: {save_err}")
            else:
                # If check_login_status_playwright returned False
                logger.error("LinkedIn session check failed. Cannot proceed to profile scraping.")

        # --- General Error Handling ---
        except Exception as e:
            # Catch-all for errors during setup or if cookie loading failed explicitly
            logger.error(f"An error occurred in the main execution block: {e}")
            traceback.print_exc()

        # --- Cleanup ---
        finally:
            # This block executes whether errors occurred or not
            logger.info("Starting cleanup...")
            if context:
                try:
                    await context.close()
                    logger.info("Browser context closed.")
                except Exception as e:
                    logger.error(f"Error closing browser context: {e}")
            if browser:
                try:
                    await browser.close()
                    logger.info("Browser closed.")
                except Exception as e:
                    logger.error(f"Error closing browser: {e}")
            logger.info("Playwright script finished.")


# --- How to Run in Google Colab ---
# 1. Install dependencies in a cell:
#    !pip install playwright beautifulsoup4
#    !playwright install
# 2. Upload your 'cookies.json' file using the Colab file browser (left sidebar).
#    Ensure it's in the root directory or update the path in `main_playwright`.
# 3. Paste this entire Python code block into a single Colab cell.
# 4. In a *new* Colab cell below the code cell, run the main function:
#    await main_playwright()

# --- To run as a standard Python script (.py file): ---
if __name__ == "__main__":
    # Check if an event loop is already running (less common for scripts)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # No running event loop
        loop = None

    if loop and loop.is_running():
        logger.info("Async event loop already running. Scheduling task.")
        # Schedule the task in the existing loop
        tsk = loop.create_task(main_playwright())
        # Note: In a script context, you might need to run the loop until the task completes
        # loop.run_until_complete(tsk) # This depends on the outer async context
    else:
        logger.info("Starting new async event loop for main_playwright.")
        # Start a new event loop to run the async function
        asyncio.run(main_playwright())
