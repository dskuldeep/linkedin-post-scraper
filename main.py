import asyncio
import json
import logging
import time
from typing import List, Dict, Set, Optional
from dataclasses import dataclass
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, TimeoutError as PlaywrightTimeoutError
import os
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class PostEngagement:
    """Data class to store engagement information for a post"""
    post_url: str
    author_profile_url: str
    likers_profiles: Set[str]
    commenters_profiles: Set[str]
    timestamp: str
    content: str
    topic_relevance_score: float = 0.0

class LinkedInAutomation:
    def __init__(self, cookies_path: str = "cookies.json"):
        self.cookies_path = cookies_path
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_logged_in: bool = False
        
    async def initialize(self) -> bool:
        """Initialize the browser and context"""
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(
                headless=False,  # Set to True for production
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            self.context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            self.page = await self.context.new_page()
            return True
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            return False

    async def load_cookies(self) -> bool:
        """Load cookies from file"""
        try:
            if not os.path.exists(self.cookies_path):
                logger.error(f"Cookie file not found: {self.cookies_path}")
                return False
                
            with open(self.cookies_path, 'r') as file:
                cookies = json.load(file)
                
            formatted_cookies = []
            for cookie in cookies:
                if '.linkedin.com' in cookie.get('domain', ''):
                    cookie['sameSite'] = 'Lax'  # Ensure proper sameSite attribute
                    formatted_cookies.append(cookie)
                    
            await self.context.add_cookies(formatted_cookies)
            return True
        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")
            return False

    async def verify_login(self) -> bool:
        """Verify LinkedIn login status by handling the intermediate 'Sign in as' prompt and puzzle challenges"""
        try:
            logger.info("Checking LinkedIn login status...")
            feed_url = "https://www.linkedin.com/feed/"
            login_indicator_selector = "div.share-box-feed-entry__closed-share-box"
            sign_in_as_button_selector = 'button:has-text("LinkedIn User")'
            puzzle_selector = 'div[data-id="challenge"]'

            # Navigate to feed
            logger.info(f"Navigating to LinkedIn feed: {feed_url}")
            await self.page.goto(feed_url, wait_until='domcontentloaded', timeout=60000)
            await asyncio.sleep(5)  # Wait for potential redirect

            # Check if we're on a puzzle/challenge page
            try:
                is_puzzle = await self.page.locator(puzzle_selector).is_visible()
                if is_puzzle:
                    logger.info("Detected puzzle/challenge page. Waiting for user to solve it...")
                    wait_time = 0
                    max_wait_time = 120  # Wait up to 60 seconds

                    while wait_time < max_wait_time:
                        # Check if we're still on the puzzle page
                        if not await self.page.locator(puzzle_selector).is_visible():
                            logger.info("Puzzle solved! Proceeding with login verification...")
                            break
                        await asyncio.sleep(5)
                        wait_time += 5
                        logger.info(f"Still waiting for puzzle to be solved... ({wait_time}s)")
                    
                    if wait_time >= max_wait_time:
                        logger.error("Puzzle solving timeout reached")
                        return False

            except PlaywrightTimeoutError:
                logger.info("No puzzle/challenge detected")
            except Exception as e:
                logger.warning(f"Error checking for puzzle: {e}")

            # Check for and handle "Sign in as" button
            logger.info("Checking for intermediate 'Sign in as' button...")
            try:
                sign_in_button = self.page.locator(sign_in_as_button_selector)
                await sign_in_button.wait_for(state='visible', timeout=10000)
                logger.info("Intermediate 'Sign in as' button found. Clicking it...")
                await sign_in_button.click()
                logger.info("Clicked the 'Sign in as' button. Waiting for transition...")
                await asyncio.sleep(7)
            except PlaywrightTimeoutError:
                logger.info("No 'Sign in as' button found (proceeding as normal).")
            except Exception as e:
                logger.warning(f"Error handling 'Sign in as' button: {e}")

            # Check for puzzle/challenge during login
            current_url = self.page.url
            logger.info(f"Current URL after navigation: {current_url}")
            
            if any(sub in current_url for sub in ["/checkpoint/challenge"]):
                logger.info("Detected security puzzle/challenge. Waiting for manual resolution...")
                start_time = time.time()
                while time.time() - start_time < 60:  # Wait up to 60 seconds
                    current_url = self.page.url
                    if "/feed" in current_url:
                        logger.info("Puzzle resolved, proceeding with login verification...")
                        break
                    remaining = int(60 - (time.time() - start_time))
                    logger.info(f"Waiting for puzzle resolution... {remaining} seconds remaining")
                    await asyncio.sleep(5)  # Check every 5 seconds
            elif any(sub in current_url for sub in ["/login", "/authwall", "/challenge", "/checkpoint"]):
                logger.error(f"Redirected to auth page: {current_url}")
                self.is_logged_in = False
                return False

            # Final login verification
            logger.info("Looking for feed page indicator...")
            try:
                await self.page.locator(login_indicator_selector).wait_for(state='visible', timeout=30000)
                logger.info("Successfully verified LinkedIn login.")
                self.is_logged_in = True
                return True
            except Exception as e:
                logger.error(f"Failed to verify login: {str(e)}")
                self.is_logged_in = False
                return False

        except PlaywrightTimeoutError:
            logger.error("Timeout verifying login status")
            self.is_logged_in = False
            return False
        except Exception as e:
            logger.error(f"Login verification failed: {e}")
            self.is_logged_in = False
            return False

    async def extract_engagement_data(self, page: Page, post_container: Page, post_data: Dict) -> Dict:
        """Extract lists of users who liked and commented on a post."""
        try:
            # Extract likers
            reactions_locator = post_container.locator('button.social-details-social-counts__count-value')
            reactions_count = await reactions_locator.count()
            
            if reactions_count > 0 and post_data['engagement']['likes'] > 0:
                await reactions_locator.first.click()
                await page.wait_for_selector('div.artdeco-modal__content')
                
                # Wait for the list to load and scroll to load all profiles
                await page.wait_for_selector('.social-details-reactors-tab-body-list-item')
                
                # Scroll the modal to load all profiles
                modal_content = page.locator('div.artdeco-modal__content')
                last_height = 0
                while True:
                    # Get all currently loaded profile links
                    current_height = await modal_content.evaluate('el => el.scrollHeight')
                    if current_height == last_height:
                        break
                    
                    await modal_content.evaluate('el => el.scrollTo(0, el.scrollHeight)')
                    await page.wait_for_timeout(1000)  # Wait for new content to load
                    last_height = current_height
                
                # Extract profile information from the modal
                likers_locator = page.locator('.social-details-reactors-tab-body-list-item .artdeco-entity-lockup')
                likers = []
                
                for i in range(await likers_locator.count()):
                    liker = likers_locator.nth(i)
                    profile_link = liker.locator('a.link-without-hover-state').first
                    
                    url = await profile_link.get_attribute('href')
                    name = await liker.locator('.artdeco-entity-lockup__title').text_content()
                    title = await liker.locator('.artdeco-entity-lockup__caption').text_content()
                    
                    if url and name:
                        likers.append({
                            "url": url.split('?')[0],  # Remove tracking parameters
                            "name": name.strip(),
                            "title": title.strip() if title else ""
                        })
                
                if likers:
                    post_data['engagement']['likers_list'] = likers
                
                # Close modal
                await page.locator('button[aria-label="Dismiss"]').click()
            
            # Extract commenters
            comments_locator = post_container.locator('button.social-details-social-counts__comments >> text=comment')
            comments_count = await comments_locator.count()
            
            if comments_count > 0 and post_data['engagement']['comments'] > 0:
                await comments_locator.first.click()
                await page.wait_for_selector('.comments-comments-list')
                
                # Extract profile information
                commenters_locator = page.locator('.comments-comments-list >> a.app-aware-link:has(.comments-post-meta__name-text)')
                commenters = []
                
                for i in range(await commenters_locator.count()):
                    commenter = commenters_locator.nth(i)
                    url = await commenter.get_attribute('href')
                    name = await commenter.locator('.comments-post-meta__name-text').text_content()
                    if url and name:
                        commenters.append({"url": url, "name": name.strip()})
                
                if commenters:
                    post_data['engagement']['commenters_list'] = commenters
            
            return post_data
            
        except Exception as e:
            logger.error(f"Error extracting engagement data: {e}")
            return post_data

    async def process_post_html(self, container, post_number: int, keyword: str) -> Dict:
        try:
            # Get unique post id for deduplication
            post_id = await container.get_attribute('data-urn')
            await container.evaluate('''node => {
                node.classList.remove('highlight-container');
                node.classList.add('processing-container');
            }''')
            # Extract post URL and author info
            post_url_elem = container.locator('.update-components-actor__meta-link')
            post_url = await post_url_elem.get_attribute('href') if await post_url_elem.count() > 0 else None
            post_url = post_url.split('?')[0] if post_url else None
            author_info = {
                "name": await container.locator('.update-components-actor__title').text_content(),
                "profile_url": post_url,
                "title": await container.locator('.update-components-actor__description').text_content(),
                "image_url": await container.locator('.update-components-actor__avatar-image').get_attribute('src')
            }
            post_metadata = {
                "post_url": post_url,
                "timestamp": await container.locator('.update-components-actor__sub-description').text_content(),
                "visibility": "public" if await container.locator('li-icon[type="globe-americas"]').count() > 0 else "private"
            }
            content_elem = container.locator('.update-components-text')
            post_content = await content_elem.text_content() if await content_elem.count() > 0 else ""
            # Process reactions (keep your existing logic here)
            likers = []
            reactions_button = container.locator('button[data-reaction-details]')
            if await reactions_button.count() > 0:
                try:
                    await reactions_button.click()
                    await self.page.wait_for_selector('div.artdeco-modal__content', timeout=5000)
                    # Scroll modal to load all profiles (indefinite, up to 300 profiles)
                    modal = self.page.locator('div.artdeco-modal__content')
                    last_height = 0
                    scroll_attempts = 0
                    max_profiles = 500
                    while True:
                        current_height = await modal.evaluate('el => el.scrollHeight')
                        if current_height == last_height:
                            break
                        await modal.evaluate('el => el.scrollTo(0, el.scrollHeight)')
                        await asyncio.sleep(1)
                        last_height = current_height
                        # Stop if we've loaded 500 or more profiles
                        profile_container = self.page.locator('.social-details-reactors-tab-body-list-item')
                        if await profile_container.count() >= max_profiles:
                            break
                        scroll_attempts += 1
                    # Extract likers
                    profile_container = self.page.locator('.social-details-reactors-tab-body-list-item')
                    for i in range(await profile_container.count()):
                        try:
                            item = profile_container.nth(i)
                            link_elem = item.locator('a.link-without-hover-state')
                            name_elem = item.locator('.artdeco-entity-lockup__title')
                            title_elem = item.locator('.artdeco-entity-lockup__subtitle')
                            if await link_elem.count() > 0:
                                url = await link_elem.get_attribute('href')
                                name = await name_elem.text_content() if await name_elem.count() > 0 else ""
                                title = await title_elem.text_content() if await title_elem.count() > 0 else ""
                                if url:
                                    clean_name = name.split("View")[0].strip() if "View" in name else name.strip()
                                    clean_url = url.split('?')[0]
                                    likers.append({
                                        "url": clean_url,
                                        "name": clean_name,
                                        "title": title.strip()
                                    })
                        except Exception as e:
                            logger.warning(f"Error extracting liker profile {i}: {e}")
                            continue
                    await self.close_modal()
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Error processing reactions: {e}")
                    await self.close_modal()
            # Process comments
            comments = []
            # Use only the robust selector for the comment button
            comments_button = container.locator('button:has-text("comment")')
            if await comments_button.count() > 0:
                try:
                    await comments_button.first.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    await comments_button.first.click()
                    logger.info("Clicked comments button, waiting for comments section to load...")
                    await self.page.wait_for_selector('.comments-comment-list__container', timeout=7000)
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.warning(f"Could not open comments section: {e}")
            else:
                logger.warning("No visible/enabled comments button found for this post using selector 'button[data-control-name=comments]'.")
            # Now, only after clicking, try to scrape comments if section is open (global)
            comments_container = container.locator('.comments-comments-list--cr')
            processed_comment_ids = set()
            if await comments_container.count() > 0 and await comments_container.first.is_visible():
                # Recursively click 'Load more comments' until all are loaded BEFORE scraping
                load_more_attempts = 0
                while True:
                    # Try both class and text-based selectors for robustness
                    load_more_btns = comments_container.locator('button:has-text("Load more comments")')
                    btn_count = await load_more_btns.count()
                    logger.info(f"[Post {post_number}] Found {btn_count} 'Load more comments' buttons on attempt {load_more_attempts+1}")
                    found = False
                    for idx in range(btn_count):
                        btn = load_more_btns.nth(idx)
                        try:
                            visible = await btn.is_visible()
                            try:
                                disabled = await btn.is_disabled()
                            except Exception:
                                disabled = False  # If is_disabled() not supported, assume enabled
                            if visible and not disabled:
                                logger.info(f"[Post {post_number}] Attempting to click 'Load more comments' button #{idx+1} (scrolling into view)...")
                                await btn.scroll_into_view_if_needed()
                                await asyncio.sleep(0.5)
                                await btn.click(force=True)
                                logger.info(f"[Post {post_number}] Clicked 'Load more comments' button #{idx+1}")
                                await asyncio.sleep(1.2)
                                found = True
                        except Exception as e:
                            logger.warning(f"[Post {post_number}] Failed to click 'Load more comments' button #{idx+1}: {e}")
                    load_more_attempts += 1
                    if not found:
                        logger.info(f"[Post {post_number}] No more clickable 'Load more comments' buttons found after {load_more_attempts} attempts.")
                        break
                # Now scrape comments, highlight as processing, then mark as done
                comment_articles = comments_container.locator('article.comments-comment-entity:not(.comments-comment-entity--reply)')
                for j in range(await comment_articles.count()):
                    comment = comment_articles.nth(j)
                    comment_id = await comment.get_attribute('data-id') or str(j)
                    if comment_id in processed_comment_ids:
                        continue
                    processed_comment_ids.add(comment_id)
                    # Highlight the comment as processing (yellow border)
                    try:
                        await comment.evaluate('node => { node.style.border = "3px solid #ffd700"; node.style.background = "#fffbe6"; }')
                    except Exception:
                        pass
                    # Extract comment author info
                    try:
                        author_name_elem = comment.locator('.comments-comment-meta__description-title').first
                        author_profile_elem = comment.locator('.comments-comment-meta__description-container').first
                        author_title_elem = comment.locator('.comments-comment-meta__description-subtitle').first
                        author_image_elem = comment.locator('.ivm-view-attr__img-wrapper img').first
                        author = {
                            "name": await author_name_elem.text_content(timeout=5000) or "Unknown User",
                            "profile_url": await author_profile_elem.get_attribute('href', timeout=5000) or "",
                            "title": await author_title_elem.text_content(timeout=5000) or "",
                            "image_url": await author_image_elem.get_attribute('src', timeout=5000) or ""
                        }
                    except Exception:
                        author = {"name": "Unknown User", "profile_url": "", "title": "", "image_url": ""}
                    try:
                        content = await comment.locator('.comments-comment-item__main-content').first.text_content()
                    except Exception:
                        content = ""
                    try:
                        timestamp = await comment.locator('time.comments-comment-meta__data').first.text_content()
                    except Exception:
                        timestamp = ""
                    # Reactions count
                    reactions_count = 0
                    reactions_selector = '.comments-comment-social-bar__reactions-count--cr'
                    try:
                        reactions_locator = comment.locator(reactions_selector).first
                        await reactions_locator.wait_for(state='visible', timeout=2000)
                        reactions_text = await reactions_locator.text_content()
                        if reactions_text:
                            reactions_count = int(''.join(filter(str.isdigit, reactions_text)))
                    except Exception:
                        reactions_count = 0
                    # Replies
                    replies = []
                    reply_elements = comment.locator('article.comments-comment-entity--reply')
                    for k in range(await reply_elements.count()):
                        reply = reply_elements.nth(k)
                        try:
                            reply_author = {
                                "name": await reply.locator('.comments-comment-meta__description-title').first.text_content(),
                                "profile_url": await reply.locator('.comments-comment-meta__description-container').first.get_attribute('href'),
                                "title": await reply.locator('.comments-comment-meta__description-subtitle').first.text_content(),
                                "image_url": await reply.locator('.ivm-view-attr__img-wrapper img').first.get_attribute('src')
                            }
                        except Exception:
                            reply_author = {"name": "Unknown User", "profile_url": "", "title": "", "image_url": ""}
                        try:
                            reply_content = await reply.locator('.comments-comment-item__main-content').first.text_content()
                        except Exception:
                            reply_content = ""
                        try:
                            reply_timestamp = await reply.locator('time.comments-comment-meta__data').first.text_content()
                        except Exception:
                            reply_timestamp = ""
                        replies.append({
                            "author": reply_author,
                            "content": reply_content,
                            "timestamp": reply_timestamp
                        })
                    comments.append({
                        "author": author,
                        "content": content,
                        "timestamp": timestamp,
                        "reactions_count": reactions_count,
                        "replies": replies
                    })
                    # Mark comment as done (fade green)
                    try:
                        await comment.evaluate('node => { node.style.border = "3px solid #4caf50"; node.style.background = "#e8f5e9"; node.style.opacity = "0.7"; }')
                    except Exception:
                        pass
                    await asyncio.sleep(0.2)
            # Save post data to JSON immediately after scraping to prevent data loss
            post_data = {
                "post_number": post_number,
                "keyword": keyword,
                "author": author_info,
                "content": post_content.strip(),
                "metadata": post_metadata,
                "engagement": {
                    "total_likers": len(likers),
                    "total_comments": len(comments),
                    "likers": likers,
                    "comments": comments
                }
            }
            os.makedirs('posts/json', exist_ok=True)
            json_filename = f'posts/json/post_{keyword}_{post_number}_profiles.json'
            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump(post_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved post data to {json_filename}")
            return post_data
        except Exception as e:
            logger.error(f"Error processing post {post_number}: {e}")
            await self.close_modal()
            return None

    async def close_modal(self):
        """Helper method to close the modal with retries"""
        try:
            # First try the specific close button
            close_button = self.page.locator('button[data-test-modal-close-btn]')
            if await close_button.count() > 0:
                await close_button.click()
            else:
                # Fallback to generic dismiss button
                dismiss_button = self.page.locator('button[aria-label="Dismiss"]')
                if await dismiss_button.count() > 0:
                    await dismiss_button.click()
            
            # Wait for modal to fully close
            try:
                await self.page.wait_for_selector('div.artdeco-modal__content', 
                                                state='hidden', 
                                                timeout=5000)
                logger.info("Modal closed successfully")
            except PlaywrightTimeoutError:
                logger.warning("Modal may not have closed properly")
            
            # Additional pause to ensure modal is fully closed
            await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"Error closing modal: {e}")

    async def _restore_container_state(self, container, post_number):
        """Helper method to restore container state after processing"""
        try:
            await container.evaluate('''node => {
                node.classList.remove('processing-container');
                node.classList.add('highlight-container');
            }''')
            logger.info(f"Restored container state for post {post_number}")
        except Exception as e:
            logger.error(f"Error restoring container state: {e}")

    async def search_posts(self, keywords: List[str], scroll_pause_time: int = 3, idle_threshold: int = 5):
        """
        Search for posts using given keywords, continuously scroll and process posts until no new content is found.
        
        Args:
            keywords: List of keywords to search for
            scroll_pause_time: Time to pause between scrolls in seconds
            idle_threshold: Number of consecutive scrolls without new content before giving up
        """
        if not self.is_logged_in:
            logger.error("Not logged in. Cannot search posts.")
            return []

        collected_urls = []

        for keyword in keywords:
            logger.info(f"Searching for posts with keyword: '{keyword}'")
            search_url = f"https://www.linkedin.com/search/results/content/?keywords={keyword}&origin=GLOBAL_SEARCH_HEADER&sortBy=DATE"

            try:
                await self.page.goto(search_url, timeout=60000, wait_until='domcontentloaded')
                await self.page.wait_for_selector('div.search-results-container', timeout=30000)
                await asyncio.sleep(2)

                # Add highlighting style
                await self.page.evaluate('''
                    if (!document.getElementById('highlight-style')) {
                        const style = document.createElement('style');
                        style.id = 'highlight-style';
                        style.textContent = `
                            .highlight-container {
                                border: 3px solid #0a66c2 !important;
                                background-color: rgba(10, 102, 194, 0.1) !important;
                                transition: all 0.3s ease !important;
                            }
                            .processed-container {
                                opacity: 0.5 !important;
                            }
                        `;
                        document.head.appendChild(style);
                    }
                ''')

                processed_post_ids = set()
                scroll_count = 0
                last_height = 0
                no_new_content_count = 0
                current_scroll_position = 0
                scroll_step = 800  # pixels to scroll each time
                last_processed_count = 0
                
                # Scroll indefinitely until no new content is found for several consecutive attempts
                while True:
                    containers = self.page.locator('.feed-shared-update-v2[data-urn]')
                    total_containers = await containers.count()
                    if total_containers == 0:
                        logger.warning("No containers found, waiting for content to load...")
                        await asyncio.sleep(2)
                        continue
                    posts_processed_this_scroll = 0
                    for i in range(total_containers):
                        try:
                            container = containers.nth(i)
                            post_id = await container.get_attribute('data-urn')
                            if not post_id or post_id in processed_post_ids:
                                continue
                            processed_post_ids.add(post_id)
                            if not await container.is_visible():
                                continue
                            await container.scroll_into_view_if_needed()
                            await asyncio.sleep(1)
                            post_data = await self.process_post_html(container, len(processed_post_ids), keyword)
                            if post_data and post_data.get('metadata', {}).get('post_url'):
                                collected_urls.append(post_data['metadata']['post_url'])
                                logger.info(f"Successfully processed post #{len(processed_post_ids)} for keyword '{keyword}'")
                            posts_processed_this_scroll += 1
                            await container.evaluate('''node => {
                                node.classList.add('highlight-container');
                            }''')
                            await asyncio.sleep(1)
                        except Exception as e:
                            logger.warning(f"Error processing container {i + 1}: {e}")
                            continue
                    if posts_processed_this_scroll == 0:
                        no_new_content_count += 1
                        logger.info(f"No new posts processed in this scroll ({no_new_content_count}/{idle_threshold})")
                    else:
                        no_new_content_count = 0
                        logger.info(f"Processed {posts_processed_this_scroll} new posts in this scroll")
                    # Remove idle_threshold check to make scroll indefinite
                    # if no_new_content_count >= idle_threshold:
                    #     logger.info(f"No new content found after {idle_threshold} consecutive scroll attempts. Finishing search for keyword '{keyword}'.")
                    #     break
                    if len(processed_post_ids) == last_processed_count:
                        no_new_content_count += 1
                    else:
                        last_processed_count = len(processed_post_ids)
                    current_scroll_position += scroll_step
                    await self.page.evaluate(f'window.scrollTo(0, {current_scroll_position})')
                    await asyncio.sleep(scroll_pause_time)
                    new_height = await self.page.evaluate('document.documentElement.scrollHeight')
                    if new_height == last_height:
                        no_new_content_count += 1
                    else:
                        no_new_content_count = 0
                    last_height = new_height
                    scroll_count += 1
                    logger.info(f"Scrolling... #{scroll_count}, Position: {current_scroll_position}px, Processed: {len(processed_post_ids)} posts")
                    # To prevent infinite loop if truly stuck, break after a very high number of scrolls (e.g., 10,000)
                    if scroll_count > 10000:
                        logger.warning("Reached 10,000 scrolls, stopping to prevent infinite loop.")
                        break
                logger.info(f"Finished processing {len(processed_post_ids)} unique posts for keyword '{keyword}'")

            except Exception as e:
                logger.error(f"Error searching posts for keyword '{keyword}': {e}")

        # Remove duplicates from final collection
        collected_urls = list(set(collected_urls))
        logger.info(f"Total unique posts collected across all keywords: {len(collected_urls)}")
        return collected_urls

    
    async def close(self):
        """Clean up resources"""
        if self.page:
            await self.page.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()

async def main():
    """Main execution function"""
    # Initialize the automation
    linkedin = LinkedInAutomation()
    
    try:
        # Setup
        if not await linkedin.initialize():
            logger.error("Failed to initialize LinkedIn automation")
            return

        if not await linkedin.load_cookies():
            logger.error("Failed to load cookies")
            return

        if not await linkedin.verify_login():
            logger.error("Failed to verify login")
            return

        # Search keywords
        search_keywords = [
            "Maxim AI",
            # "AI Quality",
            # "AI Agent Evaluation",
            # "LLM Evaluation", # You can add more specific or broader terms
            # "Responsible AI"
        ]

        # Search for posts - now with indefinite scrolling
        # The search_posts method will run until it detects no new content
        logger.info("Beginning indefinite post scraping. Will continue until no new posts are found.")
        post_urls = await linkedin.search_posts(
            keywords=search_keywords, 
            scroll_pause_time=3,   # Time to wait between scrolls (seconds)
            idle_threshold=5       # Number of scrolls with no new content before stopping
        )
        
        print(f"Collected {len(post_urls)} post URLs.")
        if not post_urls:
            logger.error("No post URLs collected.")
            return

    except Exception as e:
        logger.error(f"Main execution error: {e}")
    finally:
        await linkedin.close()

if __name__ == "__main__":
    asyncio.run(main())