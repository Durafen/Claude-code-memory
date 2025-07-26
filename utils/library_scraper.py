#!/usr/bin/env python3
"""
Crawl4AI-powered Documentation Scraper
Clean, fast extraction with perfect code block preservation
"""

import asyncio
import json
import time
import re
from pathlib import Path
from typing import Dict, List, Set
import logging
from crawl4ai import AsyncWebCrawler

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ModernDocsScraper:
    def __init__(self, site: str = "claude-code", output_dir: str = "debug/scraped_docs", 
                 config_file: str = None, force_regenerate_markdown: bool = False):
        # Load site configurations from external JSON file
        config_path = config_file or Path(__file__).parent / "scraper_configs.json"
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.site_configs = json.load(f)
            if not isinstance(self.site_configs, dict):
                raise ValueError("Config must be a dictionary")
        except FileNotFoundError:
            print(f"❌ Config file not found: {config_path}")
            exit(1)
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON in config file: {e}")
            exit(1)
        except Exception as e:
            print(f"❌ Error loading config: {e}")
            exit(1)
        
        # Set up site configuration - handle both old and new formats
        if site not in self.site_configs:
            raise ValueError(f"Unknown site: {site}. Available: {list(self.site_configs.keys())}")
        
        self.site_config = self.site_configs[site]
        self.site_name = site
        
        # Convert new format (subdomain: [urls]) to old format for compatibility
        if isinstance(self.site_config, list):
            # New format: simple URL list
            self.config = {
                'name': f'{site} Documentation',
                'urls': self.site_config
            }
        else:
            # Old format: complex config with base_url and doc_sections
            self.config = self.site_config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Track processed content
        self.scraped_content: List[Dict] = []
        self.processed_urls: Set[str] = set()
        self.force_regenerate_markdown = force_regenerate_markdown
        
        # Load existing URL cache from stats file
        self.stats_file = self.output_dir / 'scraping_stats.json'
        self.url_content_hashes = self.load_url_cache()

    def remove_navigation_noise(self, markdown: str) -> str:
        """Remove navigation and UI noise from markdown content"""
        # Navigation patterns to remove
        noise_patterns = [
            r'Search\.\.\.\s*⌘K',
            r'Search\.\.\.',
            r'\*\s*\[Research\].*?\n',
            r'\*\s*\[Login\].*?\n', 
            r'\*\s*\[Support\].*?\n',
            r'\*\s*\[Sign up\].*?\n',
            r'Was this page helpful\?\s*Yes\s*No',
            r'x\s*linkedin\s*discord',
            r'On this page\s*\n',
            r'Copy page\s*\n',
            r'^Copy\s*$',
        ]
        
        cleaned = markdown
        for pattern in noise_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.MULTILINE | re.IGNORECASE)
        
        # Remove excessive whitespace
        cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)
        cleaned = re.sub(r'^\s+', '', cleaned, flags=re.MULTILINE)
        
        return cleaned.strip()

    def load_url_cache(self) -> Dict[str, str]:
        """Load URL content hashes from existing stats file"""
        try:
            if self.stats_file.exists():
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
                return stats.get('url_content_hashes', {})
            return {}
        except Exception as e:
            logger.warning(f"Could not load URL cache: {e}")
            return {}

    def get_content_hash(self, content: str) -> str:
        """Generate hash of content for change detection"""
        import hashlib
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def should_scrape_url(self, url: str, content: str) -> bool:
        """Check if URL should be scraped based on content changes"""
        if url not in self.url_content_hashes:
            return True  # New URL, scrape it
        
        current_hash = self.get_content_hash(content)
        cached_hash = self.url_content_hashes.get(url)
        
        if current_hash != cached_hash:
            logger.info(f"Content changed for {url}, re-scraping...")
            return True
            
        return False

    def save_hash_cache(self) -> None:
        """Save URL content hashes immediately with file locking"""
        import fcntl
        try:
            # Load existing stats or create minimal structure
            existing_stats = {}
            if self.stats_file.exists():
                try:
                    with open(self.stats_file, 'r', encoding='utf-8') as f:
                        fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
                        existing_stats = json.load(f)
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Unlock
                except:
                    pass
            
            # Simple hash cache only
            stats = {
                'url_content_hashes': self.url_content_hashes,
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
                json.dump(stats, f, indent=2)
                f.flush()  # Ensure data is written
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Unlock
        except Exception as e:
            logger.warning(f"Could not save hash cache: {e}")

    async def scrape_page(self, url: str, section: str = "unknown", semaphore: asyncio.Semaphore = None) -> Dict:
        """Scrape a single page using Crawl4AI with concurrency control"""
        # Apply concurrency control if semaphore provided
        if semaphore:
            async with semaphore:
                # Rate limiting: 1 second delay between requests
                await asyncio.sleep(1)
                return await self._scrape_page_internal(url, section)
        else:
            return await self._scrape_page_internal(url, section)
    
    async def _scrape_page_internal(self, url: str, section: str) -> Dict:
        """Internal scraping logic"""
        try:
            # First, get content to check if it changed
            from crawl4ai import CrawlerRunConfig
            
            # Real exclude selectors from actual HTML analysis of all Anthropic domains
            excluded_selectors_string = "#navbar, #navbar-transition, #sidebar, #sidebar-content, #sidebar-title, #sidebar-group, #header, #footer, .nav-logo, .nav-tabs, .nav-tabs-item, #page-context-menu, #page-context-menu-button, .header__meta_wrapper, .header__site_name, .header__logo, .jsx-cf6f0ea00fa5c760, .SiteHeader_header__JZwqp, .SiteHeader_nav__fFHf4, .SiteHeader_navList__TC1Q_, .SiteHeader_navItem__iLoj9, .SiteHeader_navText__fhzDU, .SiteHeader_navCta__EnESr, .SiteFooter_root__VoI_L, .SiteFooter_columnsWrapper__L8CP7"
            
            run_config = CrawlerRunConfig(
                excluded_selector=excluded_selectors_string,
                word_count_threshold=3,
                remove_overlay_elements=True,
                exclude_social_media_links=True,
                exclude_external_links=True,
                remove_forms=True,
                excluded_tags=['nav', 'footer', 'aside', 'header']
            )
            
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(
                    url=url,
                    config=run_config,
                    markdown_generation=True,
                    bypass_cache=True
                )
            
            if not result.success:
                logger.error(f"Failed to scrape {url}: {result.error_message}")
                return None
            
            # Check if content changed before processing (unless forcing markdown regeneration)
            if not self.force_regenerate_markdown and not self.should_scrape_url(url, result.markdown):
                logger.info(f"Content unchanged for {url}, skipping...")
                return None  # Skip unchanged content
                
            logger.info(f"Processing: {url}")
            
            # Clean the markdown content
            # clean_content = self.remove_navigation_noise(result.markdown)
            clean_content = result.markdown
            
            # Extract title from first heading
            title_match = re.search(r'^#\s+(.+)', clean_content, re.MULTILINE)
            title = title_match.group(1) if title_match else "Untitled"
            
            # Update content hash cache
            content_hash = self.get_content_hash(result.markdown)
            self.url_content_hashes[url] = content_hash
            
            # Save hash cache immediately after each successful scrape
            self.save_hash_cache()
            
            page_data = {
                'url': url,
                'title': title,
                'content': clean_content,
                'section': section,
                'site': self.config['name'],
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'success': True
            }
            
            # Save MD file immediately
            self.save_single_md_file(page_data)
            
            self.processed_urls.add(url)
            return page_data
            
        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")
            return None

    async def scrape_site_sections(self) -> None:
        """Scrape all sections defined in the site config"""
        logger.info(f"Starting scrape of {self.config['name']}")
        
        # Concurrency control - limit to 5 simultaneous connections
        semaphore = asyncio.Semaphore(5)
        tasks = []
        
        if 'urls' in self.config:
            # New format: simple URL list
            for url in self.config['urls']:
                # Skip if already processed
                if url in self.processed_urls:
                    continue
                    
                # Extract section name from URL
                section_name = url.split('/')[-1] or 'unknown'
                task = self.scrape_page(url, section_name, semaphore)
                tasks.append(task)
        else:
            # Old format: base_url + doc_sections
            for section_name, path in self.config['doc_sections'].items():
                # Build full URL
                if path.startswith('http'):
                    url = path
                else:
                    url = self.config['base_url'] + path
                
                # Skip if already processed
                if url in self.processed_urls:
                    continue
                    
                # Create scraping task with rate limiting
                task = self.scrape_page(url, section_name, semaphore)
                tasks.append(task)
        
        # Execute all scraping tasks with controlled concurrency
        logger.info(f"Processing {len(tasks)} URLs with max 5 concurrent connections and 1s rate limiting")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Collect successful results
        logger.info(f"DEBUG: Collected {len(results)} results from asyncio.gather")
        for i, result in enumerate(results):
            logger.info(f"DEBUG: Result {i}: type={type(result)}, is_dict={isinstance(result, dict)}, has_success={result.get('success') if isinstance(result, dict) else 'N/A'}")
            if isinstance(result, dict) and result and result.get('success'):
                self.scraped_content.append(result)
                logger.info(f"DEBUG: Added result {i} to scraped_content. Total: {len(self.scraped_content)}")
            elif isinstance(result, Exception):
                logger.error(f"Task failed with exception: {result}")
            elif result is None:
                logger.info(f"DEBUG: Result {i} is None - likely cached/skipped URL")
            else:
                logger.info(f"DEBUG: Result {i} rejected - not a valid dict or missing success field")

    def save_single_md_file(self, page_data: dict) -> None:
        """Save single MD file immediately after processing"""
        # Create markdown directory
        md_dir = self.output_dir / 'markdown'
        md_dir.mkdir(exist_ok=True)
        
        # Create safe filename
        title = page_data['title']
        safe_title = re.sub(r'[^a-zA-Z0-9\s-]', '', title)
        safe_title = re.sub(r'\s+', '_', safe_title)
        
        # Use hash for unique filename to avoid conflicts
        url_hash = page_data['url'].split('/')[-1]
        filename = f"{url_hash}_{safe_title}.md"
        filepath = md_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# {page_data['title']}\n\n")
            f.write(f"**URL:** {page_data['url']}\n")
            f.write(f"**Section:** {page_data['section']}\n")
            f.write(f"**Scraped:** {page_data['timestamp']}\n\n")
            f.write(page_data['content'])
        
        logger.info(f"MD file saved: {filename}")

    def save_results(self) -> None:
        """Save scraped content to markdown files"""
        logger.info(f"Saving {len(self.scraped_content)} pages to {self.output_dir}")
        
        # Create markdown directory
        md_dir = self.output_dir / 'markdown'
        md_dir.mkdir(exist_ok=True)
        
        for i, page in enumerate(self.scraped_content):
            # Create safe filename
            title = page['title']
            safe_title = re.sub(r'[^a-zA-Z0-9\s-]', '', title)
            safe_title = re.sub(r'\s+', '_', safe_title)
            filename = f"{i:03d}_{safe_title}.md"
            
            md_file = md_dir / filename
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(f"# {page['title']}\n\n")
                f.write(f"**URL:** {page['url']}\n")
                f.write(f"**Site:** {page['site']}\n")
                f.write(f"**Section:** {page['section']}\n")
                f.write(f"**Scraped:** {page['timestamp']}\n\n")
                f.write(page['content'])
        
        # Simple JSON state file - just hashes for caching
        stats = {
            'url_content_hashes': self.url_content_hashes,
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Apply same file locking as save_hash_cache()
        import fcntl
        with open(self.stats_file, 'w', encoding='utf-8') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
            json.dump(stats, f, indent=2)
            f.flush()  # Ensure data is written
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Unlock
        
        logger.info(f"Results saved to {self.output_dir}")
        logger.info(f"- Markdown files: {md_dir}")
        logger.info(f"- Statistics: {self.stats_file}")


async def main():
    """Main execution function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Modern Documentation Scraper powered by Crawl4AI')
    parser.add_argument('--output-dir', default='debug/anthropic_docs_unified', 
                       help='Output directory')
    
    args = parser.parse_args()
    
    # Always scrape all sites from config
    config_path = Path(__file__).parent / "scraper_configs.json"
    with open(config_path, 'r', encoding='utf-8') as f:
        site_configs = json.load(f)
    
    all_content = []
    all_hash_caches = {}
    all_processed_urls = set()
    
    for site_name in site_configs.keys():
        print(f"\n🔄 Scraping {site_name}...")
        try:
            scraper = ModernDocsScraper(site=site_name, output_dir=args.output_dir, force_regenerate_markdown=True)
            await scraper.scrape_site_sections()
            all_content.extend(scraper.scraped_content)
            # Preserve hash caches from all scrapers
            all_hash_caches.update(scraper.url_content_hashes)
            all_processed_urls.update(scraper.processed_urls)
            print(f"✅ {site_name}: {len(scraper.scraped_content)} pages")
            
        except Exception as e:
            print(f"❌ Error scraping {site_name}: {e}")
            continue
    
    # Save unified results with preserved hash cache
    if all_content:
        final_scraper = ModernDocsScraper(site=list(site_configs.keys())[0], output_dir=args.output_dir)
        final_scraper.scraped_content = all_content
        # Restore all collected hash caches and processed URLs
        final_scraper.url_content_hashes = all_hash_caches
        final_scraper.processed_urls = all_processed_urls
        # Override site name for unified documentation
        final_scraper.config['name'] = 'Anthropic Documentation (All Sites)'
        final_scraper.save_results()
    
    print(f"\n✅ All scraping complete! Found {len(all_content)} total pages")
    print(f"📁 Output saved to: {args.output_dir}")


if __name__ == "__main__":
    asyncio.run(main())