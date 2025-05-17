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

    async def process_post_html(self, html: str, post_number: int, keyword: str) -> Dict:
        """
        Extract specific information from the LinkedIn post HTML.
        
        Args:
            html: The raw HTML string of the post container
            post_number: A unique number for this post
            keyword: The search keyword that found this post
        """
        try:
            # Remove highlight before processing
            container = self.page.locator('div.fie-impression-container').nth(post_number - 1)
            await container.evaluate('''node => {
                node.classList.remove('highlight-container');
                node.classList.add('processing-container');
            }''')

            # Find and click the reactions button directly in the container
            reactions_button = container.locator('button[data-reaction-details]')
            
            if await reactions_button.count() > 0:
                logger.info(f"Found reactions button for post {post_number}")
                profiles = []
                
                try:
                    # Scroll the button into view and click
                    await reactions_button.scroll_into_view_if_needed()
                    await asyncio.sleep(1)  # Wait for smooth scrolling
                    await reactions_button.click()
                    
                    # Wait for modal with timeout
                    try:
                        await self.page.wait_for_selector('div.artdeco-modal__content', timeout=5000)
                        logger.info("Modal opened successfully")
                    except PlaywrightTimeoutError:
                        logger.error("Modal failed to open within timeout")
                        await self._restore_container_state(container, post_number)
                        return None
                    
                    # Wait for profiles to load
                    try:
                        await self.page.wait_for_selector('.social-details-reactors-tab-body-list-item', timeout=5000)
                    except PlaywrightTimeoutError:
                        logger.error("Profile list failed to load within timeout")
                        await self.close_modal()
                        await self._restore_container_state(container, post_number)
                        return None
                    
                    # Extract post URL and post author information
                    post_url = await container.locator('.update-components-actor__meta-link').get_attribute('href')
                    post_url = post_url.split('?')[0] if post_url else None  # Remove tracking parameters
                    
                    author_info = {
                        "name": await container.locator('.update-components-actor__title').text_content(),
                        "profile_url": post_url,
                        "title": await container.locator('.update-components-actor__description').text_content(),
                        "image_url": await container.locator('.update-components-actor__avatar-image').get_attribute('src')
                    }
                    
                    # Extract post metadata
                    post_metadata = {
                        "post_url": post_url,
                        "timestamp": await container.locator('.update-components-actor__sub-description').text_content(),
                        "visibility": "public" if await container.locator('li-icon[type="globe-americas"]').count() > 0 else "private"
                    }
                    
                    # Extract post content
                    content_elem = container.locator('.update-components-text')
                    post_content = await content_elem.text_content() if await content_elem.count() > 0 else ""
                    
                    # Extract profiles of people who liked the post
                    # First scroll the modal to load all profiles
                    modal = self.page.locator('div.artdeco-modal__content')
                    last_height = 0
                    scroll_attempts = 0
                    max_scroll_attempts = 20  # Increased to ensure we load more profiles
                    
                    logger.info("Scrolling modal to load all profiles...")
                    while scroll_attempts < max_scroll_attempts:
                        # Get current height
                        current_height = await modal.evaluate('el => el.scrollHeight')
                        
                        # Break if no more content
                        if current_height == last_height:
                            logger.info(f"No more profiles to load after {scroll_attempts} scrolls")
                            break
                        
                        # Scroll and wait for new content
                        await modal.evaluate('el => el.scrollTo(0, el.scrollHeight)')
                        await asyncio.sleep(1)  # Wait for content to load
                        
                        # Update height and counter
                        last_height = current_height
                        scroll_attempts += 1
                        logger.info(f"Modal scroll attempt {scroll_attempts}/{max_scroll_attempts}")
                    
                    # Now extract all loaded profiles
                    await asyncio.sleep(1)  # Wait for final content to settle
                    profile_container = self.page.locator('.social-details-reactors-tab-body-list-item')
                    total_profiles = await profile_container.count()
                    logger.info(f"Found {total_profiles} profiles to extract")
                    likers = []
                    
                    for i in range(total_profiles):
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
                                    # Clean up the name - remove "View X's profile" text
                                    clean_name = name.split("View")[0].strip() if "View" in name else name.strip()
                                    clean_url = url.split('?')[0]  # Remove tracking parameters
                                    likers.append({
                                        "url": clean_url,
                                        "name": clean_name,
                                        "title": title.strip()
                                    })
                        except Exception as e:
                            logger.warning(f"Error extracting liker profile {i}: {e}")
                            continue
                    
                    # Close the reactions modal before getting comments
                    await self.close_modal()
                    await asyncio.sleep(2)  # Increased wait time to ensure modal is fully closed
                    
                    # Extract comments and commenters
                    comments = []
                    comments_button = container.locator('button.social-details-social-counts__count-value:has-text("comment")')
                    if await comments_button.count() > 0:
                        try:
                            # Extract number of comments from button text
                            comments_text = await comments_button.text_content()
                            total_comments = int(''.join(filter(str.isdigit, comments_text))) if comments_text else 0
                            logger.info(f"Found {total_comments} comments to process")
                            
                            # Make sure we can see the comments button
                            await comments_button.scroll_into_view_if_needed()
                            await asyncio.sleep(1)
                            
                            # Click and wait for comments
                            await comments_button.click()
                            logger.info("Clicked comments button, waiting for comments to load...")
                            await self.page.wait_for_selector('.comments-comment-list__container', timeout=5000)
                            await asyncio.sleep(1)  # Wait for animation
                            
                            # Process each comment and its replies
                            main_comments = self.page.locator('article.comments-comment-entity:not(.comments-comment-entity--reply)')
                            for i in range(await main_comments.count()):
                                try:
                                    comment = main_comments.nth(i)
                                    
                                    # Get comment author info
                                    author_container = comment.locator('.comments-comment-meta__container')
                                    author = {
                                        "name": await author_container.locator('.comments-comment-meta__description-title').text_content(),
                                        "profile_url": await author_container.locator('.comments-comment-meta__description-container').get_attribute('href'),
                                        "title": await author_container.locator('.comments-comment-meta__description-subtitle').text_content(),
                                        "image_url": await author_container.locator('.ivm-view-attr__img-wrapper img').get_attribute('src')
                                    }
                                    
                                    # Get comment content
                                    content = await comment.locator('.comments-comment-item__main-content').text_content()
                                    timestamp = await comment.locator('time.comments-comment-meta__data').text_content()
                                    
                                    # Get reactions count
                                    reactions_text = await comment.locator('.comments-comment-social-bar__reactions-count--cr').text_content()
                                    reactions_count = int(''.join(filter(str.isdigit, reactions_text))) if reactions_text else 0
                                    
                                    # Handle "See previous replies"
                                    replies = []
                                    load_replies_button = comment.locator('button:has-text("See previous replies")')
                                    if await load_replies_button.count() > 0:
                                        await load_replies_button.click()
                                        await asyncio.sleep(1)
                                    
                                    # Get all replies for this comment
                                    reply_comments = comment.locator('article.comments-comment-entity--reply')
                                    for j in range(await reply_comments.count()):
                                        try:
                                            reply = reply_comments.nth(j)
                                            reply_data = {
                                                "author": {
                                                    "name": await reply.locator('.comments-comment-meta__description-title').text_content(),
                                                    "profile_url": await reply.locator('.comments-comment-meta__description-container').get_attribute('href'),
                                                    "title": await reply.locator('.comments-comment-meta__description-subtitle').text_content(),
                                                    "image_url": await reply.locator('.ivm-view-attr__img-wrapper img').get_attribute('src')
                                                },
                                                "content": await reply.locator('.comments-comment-item__main-content').text_content(),
                                                "timestamp": await reply.locator('time.comments-comment-meta__data').text_content(),
                                            }
                                            replies.append(reply_data)
                                        except Exception as e:
                                            logger.warning(f"Error extracting reply {j}: {e}")
                                            continue
                                    
                                    comments.append({
                                        "author": author,
                                        "content": content.strip(),
                                        "timestamp": timestamp.strip(),
                                        "reactions_count": reactions_count,
                                        "replies": replies
                                    })
                                    
                                except Exception as e:
                                    logger.warning(f"Error extracting comment {i}: {e}")
                                    continue
                            
                            # Click "Load more comments" until all comments are loaded
                            while True:
                                load_more_button = self.page.locator('button.comments-comments-list__load-more-comments-button--cr')
                                if await load_more_button.count() > 0 and await load_more_button.is_visible():
                                    await load_more_button.click()
                                    await asyncio.sleep(1)  # Wait for new comments to load
                                    logger.info("Clicked load more comments button")
                                else:
                                    break
                                
                            # Now that all comments are loaded, process them
                            await asyncio.sleep(1)  # Wait for final load
                            
                            # Extract comments
                            comment_items = self.page.locator('.comments-comment-entity')
                            
                            async def extract_comment_data(comment):
                                try:
                                    # Extract basic comment info
                                    name_elem = comment.locator('.comments-comment-meta__description-title')
                                    content_elem = comment.locator('.comments-comment-item__main-content')
                                    link_elem = comment.locator('.comments-comment-meta__description-container')
                                    title_elem = comment.locator('.comments-comment-meta__description-subtitle')
                                    time_elem = comment.locator('.comments-comment-meta__data >> time')
                                    reactions_count_elem = comment.locator('.comments-comment-social-bar__reactions-count--cr')
                                    
                                    # Get reactions count and types if available
                                    reactions_info = {
                                        "count": 0,
                                        "types": []
                                    }
                                    
                                    if await reactions_count_elem.count() > 0:
                                        count_text = await reactions_count_elem.text_content()
                                        reactions_info["count"] = int(''.join(filter(str.isdigit, count_text))) if any(c.isdigit() for c in count_text) else 0
                                        
                                        # Extract reaction types from images
                                        reaction_imgs = reactions_count_elem.locator('.reactions-icon')
                                        for i in range(await reaction_imgs.count()):
                                            img = reaction_imgs.nth(i)
                                            reaction_type = await img.get_attribute('alt')
                                            if reaction_type:
                                                reactions_info["types"].append(reaction_type)
                                    
                                    # Build comment data structure
                                    comment_data = {
                                        "author": {
                                            "name": await name_elem.text_content() if await name_elem.count() > 0 else "",
                                            "profile_url": await link_elem.get_attribute('href') if await link_elem.count() > 0 else "",
                                            "title": await title_elem.text_content() if await title_elem.count() > 0 else ""
                                        },
                                        "content": await content_elem.text_content() if await content_elem.count() > 0 else "",
                                        "timestamp": await time_elem.text_content() if await time_elem.count() > 0 else "",
                                        "reactions": reactions_info,
                                        "replies": []
                                    }
                                    
                                    # Check for replies
                                    replies_list = comment.locator('.comments-replies-list')
                                    if await replies_list.count() > 0:
                                        # Check for "See previous replies" button
                                        load_prev_button = replies_list.locator('button:has-text("See previous replies")')
                                        if await load_prev_button.count() > 0:
                                            try:
                                                await load_prev_button.click()
                                                await asyncio.sleep(1)  # Wait for previous replies to load
                                                logger.info("Clicked 'See previous replies' button")
                                            except Exception as e:
                                                logger.warning(f"Error loading previous replies: {e}")
                                        
                                        # Extract all replies
                                        reply_items = replies_list.locator('.comments-comment-entity--reply')
                                        for i in range(await reply_items.count()):
                                            reply = reply_items.nth(i)
                                            reply_data = await extract_comment_data(reply)  # Recursively extract reply data
                                            comment_data["replies"].append(reply_data)
                                    
                                    return comment_data
                                except Exception as e:
                                    logger.warning(f"Error extracting comment data: {e}")
                                    return None
                            
                            # Process all top-level comments
                            for i in range(await comment_items.count()):
                                try:
                                    comment = comment_items.nth(i)
                                    if await comment.get_attribute('class') and 'comments-comment-entity--reply' not in await comment.get_attribute('class'):
                                        comment_data = await extract_comment_data(comment)
                                        if comment_data:
                                            comments.append(comment_data)
                                except Exception as e:
                                    logger.warning(f"Error extracting comment {i}: {e}")
                                    continue
                            
                            # Close comments section
                            try:
                                back_button = self.page.locator('button.comments-comment-box__collapse-button')
                                if await back_button.count() > 0:
                                    await back_button.click()
                                    await asyncio.sleep(1)
                            except Exception as e:
                                logger.warning(f"Error closing comments: {e}")
                                
                        except Exception as e:
                            logger.error(f"Error processing comments: {e}")
                    
                    # Prepare complete post data
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
                    
                    # Save post data to JSON
                    os.makedirs('posts/json', exist_ok=True)
                    json_filename = f'posts/json/post_{keyword}_{post_number}_profiles.json'
                    
                    with open(json_filename, 'w', encoding='utf-8') as f:
                        json.dump(post_data, f, indent=2, ensure_ascii=False)
                    
                    logger.info(f"Saved {len(profiles)} profiles to {json_filename}")
                    
                finally:
                    # Always try to close the modal and restore container state
                    await self.close_modal()
                    await self._restore_container_state(container, post_number)
                    await asyncio.sleep(1)  # Wait for UI to settle
                
                return post_data
            
            else:
                logger.warning(f"No reactions button found for post {post_number}")
                await self._restore_container_state(container, post_number)
                return None
                
        except Exception as e:
            logger.error(f"Error processing post {post_number}: {e}")
            await self.close_modal()  # Try to close modal in case of error
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

    async def search_posts(self, keywords: List[str], scroll_pause_time: int = 3, max_scrolls: int = 5):
        """Search for posts using given keywords, scroll to bottom first, then process all posts."""
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

                # First scroll to the bottom
                logger.info("Scrolling to bottom to load all content...")
                last_height = await self.page.evaluate('document.documentElement.scrollHeight')
                scroll_attempts = 0
                max_attempts = max_scrolls

                while scroll_attempts < max_attempts:
                    # Scroll to bottom
                    await self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
                    await asyncio.sleep(2)

                    new_height = await self.page.evaluate('document.documentElement.scrollHeight')
                    if new_height == last_height:
                        # Wait longer at bottom to ensure all content is loaded
                        logger.info("Reached bottom, waiting 10 seconds for final content...")
                        await asyncio.sleep(10)
                        
                        # Final scroll check
                        await self.page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
                        await asyncio.sleep(2)
                        final_height = await self.page.evaluate('document.documentElement.scrollHeight')
                        
                        if final_height == new_height:
                            logger.info("No more new content loading. Starting to process posts...")
                            break

                    last_height = new_height
                    scroll_attempts += 1
                    logger.info(f"Scrolling... Attempt {scroll_attempts}/{max_attempts}")
                    await asyncio.sleep(1)

                # Now process all posts from top to bottom
                logger.info("Processing all posts...")
                await self.page.evaluate('window.scrollTo(0, 0)')
                await asyncio.sleep(2)

                # Process all containers in batches
                containers = self.page.locator('div.fie-impression-container')
                total_containers = await containers.count()
                logger.info(f"Found {total_containers} total posts to process")

                processed_containers = set()
                batch_size = 5
                batch_urls = []

                # Process containers in batches
                for i in range(0, total_containers, batch_size):
                    batch_end = min(i + batch_size, total_containers)
                    logger.info(f"Processing batch {i//batch_size + 1}, posts {i+1} to {batch_end}")

                    # Process each container in the current batch
                    for j in range(i, batch_end):
                        try:
                            container = containers.nth(j)
                            
                            # Get container details for deduplication
                            container_html = await container.evaluate('node => node.outerHTML')
                            container_id = hash(container_html)

                            if container_id in processed_containers:
                                continue

                            if await container.is_visible():
                                # Process the post and save to JSON
                                post_data = await self.process_post_html(container_html, j + 1, keyword)
                                
                                # Add highlight class
                                await container.evaluate('''node => {
                                    node.classList.add('highlight-container');
                                    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                }''')

                                if post_data and post_data.get('post_url'):
                                    batch_urls.append(post_data['post_url'])

                                processed_containers.add(container_id)
                                await asyncio.sleep(0.5)  # Short pause for visibility

                        except Exception as e:
                            logger.warning(f"Error processing container {j+1}: {e}")
                            continue

                    # Short pause between batches
                    await asyncio.sleep(1)

                collected_urls.extend(batch_urls)
                logger.info(f"Finished processing {len(processed_containers)} unique posts for keyword '{keyword}'")

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

        # Search for posts - targeting around 100 posts
        # The search_posts method already has target_post_count parameter
        post_urls = await linkedin.search_posts(search_keywords)
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