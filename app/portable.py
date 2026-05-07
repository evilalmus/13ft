import flask
import requests
import cloudscraper
from flask import request
from flask_cors import CORS
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

app = flask.Flask(__name__)
CORS(app)

# Create a cloudscraper session that mimics a modern Chrome client
scraper = cloudscraper.create_scraper(
    browser={
        "browser": "chrome",
        "platform": "windows",
        "desktop": True,
    }
)

googlebot_headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.119 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://w[...]",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}
scraper.headers.update(googlebot_headers)

# ... [rest of existing HTML and helper functions remain the same] ...

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

def bypass_paywall(url):
    """
    Bypass paywall for a given url
    """
    if url.startswith("http"):
        # Expand shortened URLs first
        url = expand_shortened_url(url)
        
        # Try with Googlebot headers first
        try:
            response = requests.get(url, headers=googlebot_headers, timeout=10)
            response.encoding = response.apparent_encoding
            
            # Check if we got a Cloudflare challenge page
            if 'cdn-cgi/challenge-platform' in response.text or response.status_code == 403 or 'Invalid domain' in response.text:
                # Fallback to cloudscraper for Cloudflare-protected sites
                response = scraper.get(url, timeout=10, headers=googlebot_headers)
                response.encoding = response.apparent_encoding
            
            html = add_base_tag(response.text, response.url)
            # Inject script wrapper for better JS handling
            html = inject_script_wrapper(html, response.url)
            return html
        except Exception as e:
            # If requests fails, try cloudscraper
            try:
                response = scraper.get(url, timeout=10, headers=googlebot_headers)
                response.encoding = response.apparent_encoding
                html = add_base_tag(response.text, response.url)
                html = inject_script_wrapper(html, response.url)
                return html
            except Exception as scraper_error:
                raise e  # Raise original error if both fail

    try:
        return bypass_paywall("https://" + url)
    except requests.exceptions.RequestException as e:
        return bypass_paywall("http://" + url)
@app.route('/')
def index():
    url = request.args.get('url')
    if url:
        try:
            return bypass_paywall(url)
        except Exception as e:
            return f"Error: {str(e)}", 500
    return "Welcome to 13ft! Use ?url=<encoded-url> to bypass paywalls", 200

@app.route('/favicon.ico')
def favicon():
    return '', 204
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
