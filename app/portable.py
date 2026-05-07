import flask
import requests
import cloudscraper
from flask import request
from flask_cors import CORS
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import json
import os
import time

app = flask.Flask(__name__)
CORS(app)

# Browserless endpoint and token
BROWSERLESS_URL = "http://browserless-v2:3000"
BROWSERLESS_TOKEN = os.environ.get('BROWSERLESS_TOKEN', '')
LAST_REQUEST_BY_HOST = {}
MIN_SECONDS_BETWEEN_HOST_REQUESTS = 8
SNAPSHOT_CACHE = {}
SNAPSHOT_CACHE_TTL_SECONDS = 300

# Create a cloudscraper session that mimics a modern Chrome client
scraper = cloudscraper.create_scraper(
    browser={
        "browser": "chrome",
        "platform": "windows",
        "desktop": True,
    }
)

googlebot_headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.119 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}
scraper.headers.update(googlebot_headers)

def redact_secret(value, visible=4):
    """
    Redact secrets in logs while leaving enough characters to confirm
    the expected value is being used.
    """
    if not value:
        return ""

    value = str(value)

    if len(value) <= visible * 2:
        return "***"

    return f"{value[:visible]}...{value[-visible:]}"


def log_browserless_request(endpoint, headers, payload, timeout):
    """
    Log the exact Browserless request details without exposing the full token.
    """
    safe_endpoint = endpoint

    if BROWSERLESS_TOKEN:
        safe_endpoint = safe_endpoint.replace(
            BROWSERLESS_TOKEN,
            redact_secret(BROWSERLESS_TOKEN)
        )

    safe_headers = dict(headers)

    if "Authorization" in safe_headers:
        safe_headers["Authorization"] = "Bearer " + redact_secret(BROWSERLESS_TOKEN)

    print("\n=== Browserless Request ===", flush=True)
    print(f"Endpoint: {safe_endpoint}", flush=True)
    print(f"Timeout: {timeout}", flush=True)
    print("Headers:", flush=True)
    print(json.dumps(safe_headers, indent=2), flush=True)
    print("Payload:", flush=True)
    print(json.dumps(payload, indent=2), flush=True)
    print("===========================\n", flush=True)
    
def throttle_host(url):
    """
    Polite per-host pacing so the app does not hammer the same site repeatedly.
    """
    host = urlparse(url).netloc.lower()
    now = time.time()

    last = LAST_REQUEST_BY_HOST.get(host, 0)
    elapsed = now - last

    if elapsed < MIN_SECONDS_BETWEEN_HOST_REQUESTS:
        sleep_for = MIN_SECONDS_BETWEEN_HOST_REQUESTS - elapsed
        print(f"Throttling {host} for {sleep_for:.1f}s")
        time.sleep(sleep_for)

    LAST_REQUEST_BY_HOST[host] = time.time()

def get_cached_snapshot(url):
    entry = SNAPSHOT_CACHE.get(url)
    if not entry:
        return None

    created_at, html = entry
    if time.time() - created_at > SNAPSHOT_CACHE_TTL_SECONDS:
        SNAPSHOT_CACHE.pop(url, None)
        return None

    return html

def set_cached_snapshot(url, html):
    SNAPSHOT_CACHE[url] = (time.time(), html)
    
def expand_shortened_url(url):
    """
    Expand shortened URLs (e.g., bit.ly, tinyurl, etc.)
    """
    try:
        response = requests.head(
            url,
            allow_redirects=True,
            timeout=(3, 5),
            headers=googlebot_headers
        )
        return response.url
    except Exception as e:
        # If expansion fails, return original URL
        return url

def render_with_browserless(url):
    """
    Render a page using browserless-v2 to execute JavaScript.
    """
    try:
        if not BROWSERLESS_TOKEN:
            print(
                "Browserless token is not set. Set BROWSERLESS_TOKEN in the 13ft container.",
                flush=True
            )
            return None

        payload = {
            "url": url,
            "gotoOptions": {
                "waitUntil": ["domcontentloaded"],
                "timeout": 45000
            },
            "waitForTimeout": 5000,
            "bestAttempt": True,
            "rejectResourceTypes": [
                "media"
            ],
            "setExtraHTTPHeaders": googlebot_headers
        }

        headers = {
            "Cache-Control": "no-cache",
            "Content-Type": "application/json"
        }

        endpoint = (
            f"{BROWSERLESS_URL}/content"
            f"?token={BROWSERLESS_TOKEN}"
            f"&timeout=60000"
            f"&blockAds=true"
        )

        request_timeout = 70

        log_browserless_request(
            endpoint=endpoint,
            headers=headers,
            payload=payload,
            timeout=request_timeout
        )

        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=request_timeout
        )

        print("\n=== Browserless Response ===", flush=True)
        print(f"Status: {response.status_code}", flush=True)
        print("Response headers:", flush=True)
        print(json.dumps(dict(response.headers), indent=2), flush=True)
        print(f"Body length: {len(response.text)} bytes", flush=True)
        print(f"Body preview: {response.text[:500]}", flush=True)
        print("============================\n", flush=True)

        if response.status_code == 200:
            return response.text

        print(
            f"Browserless error: {response.status_code} - {response.text}",
            flush=True
        )
        return None

    except Exception as e:
        print(
            f"Browserless rendering failed: {type(e).__name__}: {e}",
            flush=True
        )
        return None
def add_base_tag(html_content, original_url):
    soup = BeautifulSoup(html_content, 'html.parser')
    parsed_url = urlparse(original_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
    
    # Handle paths that are not root, e.g., "https://x.com/some/path/w.html"
    if parsed_url.path and not parsed_url.path.endswith('/'):
        base_url = urljoin(base_url, parsed_url.path.rsplit('/', 1)[0] + '/')
    base_tag = soup.find('base')
    
    print(base_url)
    if not base_tag:
        new_base_tag = soup.new_tag('base', href=base_url)
        if soup.head:
            soup.head.insert(0, new_base_tag)
        else:
            head_tag = soup.new_tag('head')
            head_tag.insert(0, new_base_tag)
            soup.insert(0, head_tag)
    
    return str(soup)

def inject_script_wrapper(html_content, target_url):
    """
    Inject a script that rewrites fetch/XHR calls to proxy through our server
    This helps with JS-heavy sites that make API calls
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Create a script that intercepts fetch and XHR calls
    proxy_script = f"""
    <script>
    (function() {{
        const targetOrigin = '{urlparse(target_url).netloc}';
        const proxyBase = window.location.origin;
        
        // Override fetch
        const originalFetch = window.fetch;
        window.fetch = function(...args) {{
            let url = args[0];
            if (typeof url === 'string' && !url.startsWith('blob:') && !url.startsWith('data:')) {{
                if (!url.startsWith('http')) {{
                    url = 'https://' + targetOrigin + (url.startsWith('/') ? url : '/' + url);
                }}
                if (url.includes(targetOrigin)) {{
                    args[0] = proxyBase + '/?url=' + encodeURIComponent(url);
                }}
            }}
            return originalFetch.apply(this, args);
        }};
        
        // Override XMLHttpRequest
        const originalOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
            if (typeof url === 'string' && !url.startsWith('blob:') && !url.startsWith('data:')) {{
                if (!url.startsWith('http')) {{
                    url = 'https://' + targetOrigin + (url.startsWith('/') ? url : '/' + url);
                }}
                if (url.includes(targetOrigin)) {{
                    url = proxyBase + '/?url=' + encodeURIComponent(url);
                }}
            }}
            return originalOpen.call(this, method, url, ...rest);
        }};
    }})();
    </script>
    """
    
    # Insert at the beginning of head or body
    if soup.head:
        soup.head.insert(0, BeautifulSoup(proxy_script, 'html.parser'))
    elif soup.body:
        soup.body.insert(0, BeautifulSoup(proxy_script, 'html.parser'))
    else:
        soup.insert(0, BeautifulSoup(proxy_script, 'html.parser'))
    
    return str(soup)

def bypass_paywall(url, snapshot=False):
    """
    Fetch/render a URL using either Browserless snapshot mode or regular proxy mode.
    """
    url = normalize_url(url)
    url = expand_shortened_url(url)
    throttle_host(url)
    
    # Try rendering with browserless first for JavaScript-heavy sites
    if snapshot:
        cached = get_cached_snapshot(url)
        if cached:
            print(f"Using cached snapshot for {url}")
            return cached
        
        try:
            html = render_with_browserless(url)
            if html:
                html = make_static_snapshot(html)
                html = add_base_tag(html, url)
                set_cached_snapshot(url, html)
#                   html = add_base_tag(html, url)
#                   html = inject_script_wrapper(html, url)
                return html
        except Exception as e:
            print(f"Browserless snapshot attempt failed: {e}")
        # Fallback to traditional requests with Googlebot headers
    try:
        response = requests.get(url, headers=googlebot_headers, timeout=10)
        response.encoding = response.apparent_encoding
        # Check if we got a Cloudflare challenge page
        if 'cdn-cgi/challenge-platform' in response.text or response.status_code == 403 or 'Invalid domain' in response.text:
            # Fallback to cloudscraper for Cloudflare-protected sites
            response = scraper.get(url, timeout=10, headers=googlebot_headers)
            response.encoding = response.apparent_encoding            
        html = add_base_tag(response.text, response.url)
        html = inject_script_wrapper(html, response.url)
        return html

    except Exception as first_error:
        print(
            f"Regular requests mode failed: {type(first_error).__name__}: {first_error}",
            flush=True
        )

        try:
            response = scraper.get(
                url,
                timeout=(5, 15),
                headers=googlebot_headers,
                allow_redirects=True
            )
            response.encoding = response.apparent_encoding

            html = add_base_tag(response.text, response.url)
            html = inject_script_wrapper(html, response.url)
            return html

        except Exception as scraper_error:
            print(
                f"Cloudscraper fallback failed: {type(scraper_error).__name__}: {scraper_error}",
                flush=True
            )
            return (
                f"Error loading URL in regular proxy mode: {first_error}",
                502
            )
            
def make_static_snapshot(html_content):
    """
    Browserless already executed JavaScript. Remove scripts so the returned
    page does not try to hydrate/reboot inside the proxy origin.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup.find_all("script"):
        tag.decompose()

    for tag in soup.find_all("noscript"):
        tag.decompose()

    return str(soup)

def normalize_url(url):
    """
    Normalize user-entered URLs without recursive retry loops.
    """
    url = (url or "").strip()

    if not url:
        raise ValueError("No URL provided")

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("http://") or url.startswith("https://"):
        return url

    return "https://" + url
    
@app.route('/', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
def index():
    url = request.args.get('url')
    snapshot = request.args.get('snapshot') == '1'

    if url:
        try:
            return bypass_paywall(url, snapshot=snapshot)
        except Exception as e:
            return f"Error: {str(e)}", 500
    
    # Serve the GUI when no URL is provided
    gui_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>13ft - Paywall Bypass</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                margin: 0;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            }
            .container {
                background: white;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.2);
                max-width: 500px;
                width: 90%;
            }
            h1 {
                color: #333;
                text-align: center;
                margin: 0 0 20px 0;
            }
            p {
                color: #666;
                text-align: center;
                margin: 0 0 20px 0;
            }
            .input-group {
                display: flex;
                gap: 10px;
                margin-bottom: 20px;
            }
            input[type="text"] {
                flex: 1;
                padding: 12px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 14px;
            }
            button {
                padding: 12px 20px;
                background: #667eea;
                color: white;
                border: none;
                border-radius: 5px;
                cursor: pointer;
                font-size: 14px;
                font-weight: bold;
            }
            button:hover {
                background: #764ba2;
            }
            .checkbox-row {
                display: flex;
                align-items: center;
                gap: 8px;
                color: #444;
                font-size: 14px;
                margin: 10px 0 8px 0;
            }
            .checkbox-row input {
                width: 16px;
                height: 16px;
            }
            .hint {
                color: #777;
                font-size: 12px;
                text-align: left;
                margin-top: 8px;
                line-height: 1.4;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>13ft</h1>
            <p>Bypass paywalls and access restricted content</p>
            <form method="GET" action="/">
                <div class="input-group">
                    <input type="text" name="url" placeholder="Enter URL..." required>
                    <button type="submit">Load</button>
                </div>

                <label class="checkbox-row">
                    <input type="checkbox" name="snapshot" value="1" checked>
                    Use Browserless snapshot mode
                </label>

                <p class="hint">
                    Snapshot mode renders the page first, then serves a static copy. Turn it off for regular proxy mode.
                </p>
            </form>
        </div>
    </body>
    </html>
    """
    return gui_html

@app.route('/favicon.ico')
def favicon():
    return '', 204
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
