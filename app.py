import os
import re
import urllib.parse
import asyncio
from typing import Dict, Optional
from itertools import cycle
import json 

# Install playwright if not present
if os.getenv("PLAYWRIGHT_INSTALL_RUN", "false").lower() != "true":
    os.system("playwright install")
    os.environ["PLAYWRIGHT_INSTALL_RUN"] = "true"

from flask import Flask, request, jsonify
from bs4 import BeautifulSoup, NavigableString
from playwright.async_api import async_playwright

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Credential and State Management ---
class CredentialRevolver:
    def __init__(self, proxy_string: str):
        self.proxies = self._parse_proxies(proxy_string)
        self.proxy_cycler = cycle(self.proxies) if self.proxies else None
    def _parse_proxies(self, proxy_string: str):
        proxies = []
        if not proxy_string: return proxies
        for line in proxy_string.strip().splitlines():
            try:
                parsed = urllib.parse.urlparse(f"//{line.strip()}")
                if not parsed.hostname or not parsed.port: continue
                server = f"http://{parsed.hostname}:{parsed.port}"
                proxy_dict = {"server": server}
                if parsed.username: proxy_dict["username"] = urllib.parse.unquote(parsed.username)
                if parsed.password: proxy_dict["password"] = urllib.parse.unquote(parsed.password)
                proxies.append(proxy_dict)
            except Exception: pass
        return proxies
    def get_next(self) -> Optional[Dict]:
        return next(self.proxy_cycler) if self.proxy_cycler else None
    def count(self) -> int:
        return len(self.proxies)

REVOLVER = CredentialRevolver(os.getenv("PROXY_LIST", ""))

SEARCH_ENGINES = {
    "Google": "https://www.google.com/search?q={query}&hl=en", "DuckDuckGo": "https://duckduckgo.com/html/?q={query}", "Bing": "https://www.bing.com/search?q={query}", "Brave": "https://search.brave.com/search?q={query}", "Ecosia": "https://www.ecosia.org/search?q={query}", "Yahoo": "https://search.yahoo.com/search?p={query}", "Startpage": "https://www.startpage.com/sp/search?q={query}", "Qwant": "https://www.qwant.com/?q={query}", "Swisscows": "https://swisscows.com/web?query={query}", "You.com": "https://you.com/search?q={query}", "SearXNG": "https://searx.be/search?q={query}", "MetaGer": "https://metager.org/meta/meta.ger-en?eingabe={query}", "Yandex": "https://yandex.com/search/?text={query}", "Baidu": "https://www.baidu.com/s?wd={query}", "Perplexity": "https://www.perplexity.ai/search?q={query}",
}

class HTML_TO_MARKDOWN_CONVERTER:
    def __init__(self, soup: BeautifulSoup, base_url: str): self.soup = soup; self.base_url = base_url
    def _cleanup_html(self): selectors_to_remove = ['nav', 'footer', 'header', 'aside', 'form', 'script', 'style', 'svg', 'button', 'input', 'textarea', '[role="navigation"]', '[role="search"]', '[id*="comment"]', '[class*="comment-"]', '[id*="sidebar"]', '[class*="sidebar"]', '[id*="related"]', '[class*="related"]', '[id*="share"]', '[class*="share"]', '[id*="social"]', '[class*="social"]', '[id*="cookie"]', '[class*="cookie"]', '[aria-hidden="true"]']; [element.decompose() for selector in selectors_to_remove for element in self.soup.select(selector)]
    def convert(self): self._cleanup_html(); content_node = self.soup.find('main') or self.soup.find('article') or self.soup.find('body'); return re.sub(r'\n{3,}', '\n\n', self._process_node(content_node)).strip() if content_node else ""
    def _process_node(self, element):
        if isinstance(element, NavigableString): return re.sub(r'\s+', ' ', element.strip())
        if element.name is None or not element.name: return ''
        inner_md = " ".join(self._process_node(child) for child in element.children).strip()
        if element.name in ['p', 'div', 'section']: return f"\n\n{inner_md}\n\n"
        if element.name == 'h1': return f"\n\n# {inner_md}\n\n"
        if element.name == 'h2': return f"\n\n## {inner_md}\n\n"
        if element.name == 'h3': return f"\n\n### {inner_md}\n\n"
        if element.name in ['h4', 'h5', 'h6']: return f"\n\n#### {inner_md}\n\n"
        if element.name == 'li': return f"* {inner_md}\n"
        if element.name in ['ul', 'ol']: return f"\n{inner_md}\n"
        if element.name == 'blockquote': return f"> {inner_md.replace(chr(10), chr(10) + '> ')}\n\n"
        if element.name == 'hr': return "\n\n---\n\n"
        if element.name == 'table': header = " | ".join(f"**{th.get_text(strip=True)}**" for th in element.select('thead th, tr th')); separator = " | ".join(['---'] * len(header.split('|'))); rows = [" | ".join(td.get_text(strip=True) for td in tr.find_all('td')) for tr in element.select('tbody tr')]; return f"\n\n{header}\n{separator}\n" + "\n".join(rows) + "\n\n"
        if element.name == 'pre': return f"\n```\n{element.get_text(strip=True)}\n```\n\n"
        if element.name == 'code': return f"`{inner_md}`"
        if element.name in ['strong', 'b']: return f"**{inner_md}**"
        if element.name in ['em', 'i']: return f"*{inner_md}*"
        if element.name == 'a': href = element.get('href', ''); full_href = urllib.parse.urljoin(self.base_url, href); return f"[{inner_md}]({full_href})"
        if element.name == 'img': src = element.get('src', ''); alt = element.get('alt', 'Image').strip(); full_src = urllib.parse.urljoin(self.base_url, src); return f"\n\n![{alt}]({full_src})\n\n"
        return inner_md

# --- Core Web Browsing Logic ---
async def perform_web_browse(action: str, query: str, browser_name: str, search_engine: str):
    playwright = None
    browser = None
    try:
        playwright = await async_playwright().start()
        
        browser_key = browser_name.lower()
        browser_map = {'firefox': playwright.firefox, 'chromium': playwright.chromium, 'webkit': playwright.webkit}
        browser_launcher = browser_map.get(browser_key)
        
        if not browser_launcher:
            raise ValueError(f"Invalid browser name: {browser_name}")

        # <<< CHANGE >>> Launch the browser inside the function.
        launch_args = ['--no-sandbox'] if browser_key == 'chromium' else []
        browser = await browser_launcher.launch(headless=True, args=launch_args)

        if action == "Scrape":
            url = query if query.startswith(('http://', 'https://')) else f"http://{query}"
        else: # action == "Search"
            url_template = SEARCH_ENGINES.get(search_engine)
            if not url_template:
                return {"status": "error", "query": query, "error_message": f"Invalid search engine: '{search_engine}'."}
            url = url_template.format(query=urllib.parse.quote_plus(query))

        proxy_config = REVOLVER.get_next()
        proxy_server_used = proxy_config["server"] if proxy_config else "Direct Connection"
        
        context_args = {'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', 'java_script_enabled': True, 'ignore_https_errors': True, 'bypass_csp': True, 'accept_downloads': False}
        if proxy_config: context_args['proxy'] = proxy_config

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        try:
            response = await page.goto(url, wait_until='domcontentloaded', timeout=25000)
            html_content = await page.content()
            if any(phrase in html_content for phrase in ["unusual traffic", "CAPTCHA", "are you human", "not a robot"]):
                raise Exception(f"Anti-bot measure detected on {page.url}. Try another search engine or proxy.")
            final_url, title = page.url, await page.title() or "No Title"
            soup = BeautifulSoup(html_content, 'lxml')
            converter = HTML_TO_MARKDOWN_CONVERTER(soup, base_url=final_url)
            markdown_text = converter.convert()
            status_code = response.status if response else 0
            return {"status": "success", "query": query, "action": action, "final_url": final_url, "page_title": title, "http_status": status_code, "proxy_used": proxy_server_used, "markdown_content": markdown_text}
        except Exception as e:
            error_message = str(e).splitlines()[0]
            if "Timeout" in error_message:
                return {"status": "error", "query": query, "proxy_used": proxy_server_used, "error_message": f"Navigation Timeout: The page for '{query}' took too long to load."}
            return {"status": "error", "query": query, "proxy_used": proxy_server_used, "error_message": error_message}
        finally:
            if 'page' in locals() and not page.is_closed(): await page.close()
            if 'context' in locals(): await context.close()

    except Exception as e:
        app.logger.error(f"A critical error occurred in perform_web_browse: {e}", exc_info=True)
        return {"status": "error", "query": query, "error_message": f"Failed to initialize browser resources: {str(e).splitlines()[0]}"}
    
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


@app.route('/', methods=['GET'])
def index():
    return json.dumps({ "status": "online", "message": "Welcome to the Web Browse API!", "api_endpoint": "/web_browse", "instructions": "Send a POST request to /web_browse with a JSON payload to use the service.", "payload_format": { "action": "string (required: 'Search' or 'Scrape')", "query": "string (required: a search term or a full URL)", "browser": "string (optional, default: 'firefox'; options: 'firefox', 'chromium', 'webkit')", "search_engine": "string (optional, default: 'DuckDuckGo'; see code for all options)" }, "example_curl": """curl -X POST YOUR_SPACE_URL/web_browse -H "Content-Type: application/json" -d '{"action": "Search", "query": "latest news on AI", "browser": "webkit"}'""" }, indent=4)

@app.route('/web_browse', methods=['POST'])
def web_browse():
    if not request.is_json: return jsonify({"status": "error", "error_message": "Invalid input: payload must be JSON"}), 400
    data = request.get_json()
    action = data.get('action')
    query = data.get('query')
    browser = data.get('browser', 'firefox')
    search_engine = data.get('search_engine', 'DuckDuckGo')
    if not action or not query: return jsonify({"status": "error", "error_message": "Missing required parameters: 'action' and 'query' are mandatory."}), 400
    if action not in ["Search", "Scrape"]: return jsonify({"status": "error", "error_message": "Invalid 'action'. Must be 'Search' or 'Scrape'."}), 400
    try:
        result = asyncio.run(perform_web_browse(action, query, browser, search_engine))
        response_status_code = 200 if result.get("status") == "success" else 500
        return jsonify(result), response_status_code
    except Exception as e:
        app.logger.error(f"An unexpected server error occurred: {e}", exc_info=True)
        return jsonify({"status": "error", "query": query, "error_message": f"An unexpected server error occurred: {str(e)}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    print(f"Flask server starting on port {port}... {REVOLVER.count()} proxies loaded.")
    app.run(host='0.0.0.0', port=port)
