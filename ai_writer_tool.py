import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
import base64
import time
import json
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
import re
from urllib.parse import urlparse, urljoin, unquote
import os
import sys

# --- Configuration & Storage ---
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            return {"sites": [], "api_key": "", "model": "gemini-2.0-flash-lite-preview-02-05"}
    return {"sites": [], "api_key": "", "model": "gemini-2.0-flash-lite-preview-02-05"}

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

# Load config at start
config = load_config()

# --- Configuration & UI Setup ---
st.set_page_config(page_title="ScrapWrite", page_icon=None, layout="wide")

# Custom CSS
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&display=swap');
    
    html, body, [class*="css"], .stTextInput, .stTextArea, .stSelectbox, .stButton, .stCheckbox, .stColorPicker {
        font-family: 'JetBrains Mono', 'Courier New', monospace !important;
        font-size: 13px;
    }
    h1 { 
        font-size: 28px !important; 
        text-align: center !important;
        margin-bottom: 20px !important;
    }
    h2, h3 { font-size: 18px !important; }
    
    .stButton button {
        border-radius: 4px;
        font-weight: bold;
    }
    
    /* Log Box Styling */
    .log-box {
        background-color: #f0f2f6;
        border: 1px solid #d6d6d6;
        border-radius: 5px;
        padding: 10px;
        height: 200px;
        overflow-y: auto;
        font-family: 'JetBrains Mono', monospace;
        font-size: 12px;
        white-space: pre-wrap;
        margin-top: 10px;
        color: #333;
    }
    .dark-mode .log-box {
        background-color: #262730;
        border-color: #464b59;
        color: #fafafa;
    }

    /* Compact spacing */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1>Scrap, ReWrite, Publish</h1>", unsafe_allow_html=True)

# --- Functions ---

def clean_url(url):
    return url.strip()

def get_wp_categories(wp_url, wp_user, wp_password):
    if not wp_url.endswith('/'): wp_url += '/'
    api_endpoint = f"{wp_url}wp-json/wp/v2/categories?per_page=100"
    credentials = f"{wp_user.strip()}:{wp_password.strip()}"
    token = base64.b64encode(credentials.encode()).decode('utf-8')
    headers = {'Authorization': f'Basic {token}'}
    try:
        r = requests.get(api_endpoint, headers=headers, timeout=10)
        r.raise_for_status()
        return {c['name']: c['id'] for c in r.json()}
    except:
        return {}

def get_wp_authors(wp_url, wp_user, wp_password):
    if not wp_url.endswith('/'): wp_url += '/'
    api_endpoint = f"{wp_url}wp-json/wp/v2/users?per_page=100"
    credentials = f"{wp_user.strip()}:{wp_password.strip()}"
    token = base64.b64encode(credentials.encode()).decode('utf-8')
    headers = {'Authorization': f'Basic {token}'}
    try:
        r = requests.get(api_endpoint, headers=headers, timeout=10)
        r.raise_for_status()
        return {u['name']: u['id'] for u in r.json()}
    except:
        return {}

def process_image(img_url, wp_url, wp_user, wp_password, watermark_text=None, wm_color="white", wm_bg_color=None):
    """Downloads, processes (WebP, fit 1280x720 transparent), and uploads image to WP."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(img_url, headers=headers, stream=True, timeout=10)
        r.raise_for_status()
        
        img = Image.open(io.BytesIO(r.content))
        
        # 1. Resize/Fit to 1280x720 (Logic: Resize image to fit INSIDE 1280x720 first)
        target_size = (1280, 720)
        img.thumbnail(target_size, Image.Resampling.LANCZOS) # Modifies img in place to fit
        
        # 2. Watermark (Apply to the resized image BEFORE pasting on transparent bg)
        if watermark_text:
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            draw = ImageDraw.Draw(img)
            try:
                font_size = 24
                font = ImageFont.truetype("arial.ttf", font_size)
            except IOError:
                font = ImageFont.load_default()
            
            text_bbox = draw.textbbox((0, 0), watermark_text, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            
            # Position: Bottom Right of the ACTUAL image
            x = img.width - text_width - 10
            y = img.height - text_height - 10
            
            # Ensure x,y are positive
            x = max(0, x)
            y = max(0, y)
            
            # Background for text
            if wm_bg_color: # If user selected a color (default transparent handled by UI logic)
                 padding = 4
                 bg_box = [x - padding, y - padding, x + text_width + padding, y + text_height + padding]
                 draw.rectangle(bg_box, fill=wm_bg_color)
            
            draw.text((x, y), watermark_text, fill=wm_color, font=font)

        # 3. Create Transparent Canvas 1280x720 and Paste
        new_img = Image.new("RGBA", target_size, (0, 0, 0, 0))
        
        # Center position
        left = (target_size[0] - img.width) // 2
        top = (target_size[1] - img.height) // 2
        new_img.paste(img, (left, top))
        
        # 4. Save as WebP
        buffer = io.BytesIO()
        new_img.save(buffer, format="WEBP", quality=85) 
        image_data = buffer.getvalue()
        
        # 5. Filename & Metadata
        parsed = urlparse(img_url)
        base_name = os.path.basename(parsed.path)
        base_name = unquote(base_name)
        name_no_ext = os.path.splitext(base_name)[0]
        
        # Clean filename
        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '', name_no_ext)
        if not clean_name or len(clean_name) < 3:
            clean_name = f"img_{int(time.time())}"
            
        filename = f"{clean_name}.webp"
        
        # Alt Text / Title / Desc
        meta_text = clean_name.replace('-', ' ').replace('_', ' ')
        
        # Upload
        if not wp_url.endswith('/'): wp_url += '/'
        api_endpoint = f"{wp_url}wp-json/wp/v2/media"
        
        credentials = f"{wp_user.strip()}:{wp_password.strip()}"
        token = base64.b64encode(credentials.encode()).decode('utf-8')
        
        headers = {
            'Authorization': f'Basic {token}',
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Type': 'image/webp'
        }
        
        r_up = requests.post(api_endpoint, headers=headers, data=image_data)
        r_up.raise_for_status()
        result = r_up.json()
        
        # Update Metadata
        media_id = result.get('id')
        if media_id:
             update_data = {
                 'alt_text': meta_text,
                 'description': meta_text,
                 'title': meta_text,
                 'caption': meta_text
             }
             try:
                 requests.post(f"{api_endpoint}/{media_id}", headers=headers, json=update_data)
             except:
                 pass

        return result.get('source_url') 
        
    except Exception as e:
        # print(f"Image Error: {e}") 
        return None

def clean_link(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

def scrape_content(url, process_imgs=False, keep_links=False, link_opts=None, wp_config=None, watermark_text=None, wm_opts=None):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        for script in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            script.decompose()
            
        content_area = soup.find('main') or soup.find('article') or soup.find('div', class_='content') or soup.find('div', class_='entry-content') or soup.body
        
        if not content_area:
            return "Error: Could not find content."
        
        # Cleaning
        bad_classes = re.compile(r'related|widget|share|sidebar|cta|newsletter|author-box|post-navigation|jp-relatedposts|meta|date|time|posted-on|entry-meta|breadcrumbs|toc|table-of-contents')
        for junk in content_area.find_all(class_=bad_classes):
            junk.decompose()
            
        for tag in content_area.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div', 'p', 'span', 'small', 'li']):
            text = tag.get_text().strip().lower()
            if any(x in text for x in ['table of contents', 'contents', 'you may also like', 'related posts', 'find out more', 'schedule a call', 'read more']):
                if len(text) < 100: 
                    tag.decompose()
                    continue
            if any(x in text for x in ['min read', 'updated on', 'published on', 'written by', 'author:']):
                 if len(text) < 60:
                     tag.decompose()

        # Remove internal links / Read More
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        for a in content_area.find_all('a', href=True):
            href = a['href']
            if domain in href or href.startswith('/'):
                parent = a.parent
                if parent and parent.name != 'body': # Safety check
                    parent_text = parent.get_text().strip().lower()
                    if any(k in parent_text for k in ['read more', 'also read', 'related', 'more on', 'apni aro', 'dekhte paren']):
                         parent.decompose() 
                    else:
                         a.decompose()
        
        # Empty tags
        for empty in content_area.find_all(['p', 'div', 'span', 'li']):
            if not empty.get_text(strip=True) and not empty.find('img') and not empty.find('iframe'):
                empty.decompose()

        # Links
        if keep_links:
            for a in content_area.find_all('a', href=True):
                a['href'] = clean_link(a['href'])
                if link_opts:
                    if link_opts.get('new_tab'): a['target'] = "_blank"
                    rels = []
                    if link_opts.get('new_tab'): rels.extend(["noopener", "noreferrer"])
                    if link_opts.get('nofollow'): rels.append("nofollow")
                    if link_opts.get('sponsored'): rels.append("sponsored")
                    if rels: a['rel'] = " ".join(rels)
        else:
            for a in content_area.find_all('a'):
                a.replace_with(a.get_text())

        # Images
        if process_imgs and wp_config:
            imgs = content_area.find_all('img')
            for img in imgs:
                src = img.get('src') or img.get('data-src')
                if src:
                    full_url = urljoin(url, src)
                    wm_color = wm_opts.get('color', 'white') if wm_opts else 'white'
                    wm_bg = wm_opts.get('bg_color', None) if wm_opts else None
                    
                    new_url = process_image(full_url, wp_config['url'], wp_config['user'], wp_config['pass'], watermark_text, wm_color, wm_bg)
                    if new_url:
                        img['src'] = new_url
                        classes = img.get('class', [])
                        if 'aligncenter' not in classes: classes.append('aligncenter')
                        img['class'] = classes
                        # Clean attributes
                        for attr in ['srcset', 'sizes', 'width', 'height', 'loading', 'style']:
                            if attr in img.attrs: del img.attrs[attr]
                    else:
                        img.decompose()
        elif not process_imgs:
             for img in content_area.find_all('img'):
                 img.decompose()
        
        # Videos
        for iframe in content_area.find_all('iframe'):
             iframe['width'] = "1280"
             iframe['height'] = "720"
             iframe['style'] = "max-width: 100%; height: auto; aspect-ratio: 16/9;"

        return str(content_area)
    except Exception as e:
        return f"Error scraping {url}: {str(e)}"

def generate_rewrite(content, api_key, model_id):
    if not content or content.startswith("Error"): return None
    
    genai.configure(api_key=api_key)
    try:
        model = genai.GenerativeModel(model_id)
    except:
        model = genai.GenerativeModel("gemini-1.5-flash")
        
    prompt = f"""
    You are an expert SEO Content Writer. Rewrite the HTML content to be highly engaging, complete, and SEO-friendly.
    
    **CRITICAL INSTRUCTIONS:**
    1. **COMPLETENESS VERIFICATION:** You MUST cover ALL items/points from the input. Count them. If the input reviews 13 items, you MUST review all 13. Do NOT skip any.
    2. **Hyperlinks:** Be EXTREMELY careful. Only hyperlink the specific Brand Name or Key Term. DO NOT link full sentences or paragraphs.
    3. **Title:** Create a clickable H1. 50-60 chars. NO YEARS (e.g., 2025). Clean style.
    4. **Images/Videos:** KEEP ALL IMAGES and IFRAMES. Do not change SRC. Ensure they are placed naturally.
    5. **Format:** Use H2, H3, bullet points, tables where appropriate.
    6. **Clean:** Remove "Table of Contents", "Read more", CTA buttons.
    7. **Output:** ONLY HTML body (H1...End). NO <html>/<body> tags.
    
    **Input:**
    {content[:60000]} 
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating content: {str(e)}"

def upload_to_wordpress(wp_url, wp_user, wp_password, title, content, status="draft", author_id=None, category_ids=None):
    if not wp_url.endswith('/'): wp_url += '/'
    api_endpoint = f"{wp_url}wp-json/wp/v2/posts"
    
    soup = BeautifulSoup(content, 'html.parser')
    h1 = soup.find('h1')
    if h1:
        post_title = h1.get_text()
        h1.decompose()
        post_content = str(soup)
    else:
        post_title = title
        post_content = content

    credentials = f"{wp_user.strip()}:{wp_password.strip()}"
    token = base64.b64encode(credentials.encode()).decode('utf-8')
    headers = {
        'Authorization': f'Basic {token}',
        'Content-Type': 'application/json'
    }
    
    data = {'title': post_title, 'content': post_content, 'status': status}
    if author_id: data['author'] = author_id
    if category_ids: data['categories'] = category_ids
    
    try:
        r = requests.post(api_endpoint, headers=headers, json=data)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return f"Error uploading: {str(e)}"

# --- UI Layout ---

# Sidebar: Config
with st.sidebar:
    st.header("Config")
    
    with st.expander("API Key", expanded=False):
        new_key = st.text_input("Gemini Key", value=config.get("api_key", ""), type="password")
        if new_key != config.get("api_key"):
            config["api_key"] = new_key
            save_config(config)

    st.markdown("### Site Management")
    # Tabs for Site Management
    tab_add, tab_list, tab_test = st.tabs(["Add Site", "Site List", "Test Site"])
    
    with tab_add:
        with st.form("add_site_form"):
            s_name = st.text_input("Site Name")
            s_url = st.text_input("URL")
            s_user = st.text_input("Username")
            s_pass = st.text_input("App Password", type="password")
            if st.form_submit_button("Add Site"):
                if s_name and s_url and s_user and s_pass:
                    new_site = {"name": s_name, "url": s_url, "username": s_user, "password": s_pass}
                    config["sites"].append(new_site)
                    save_config(config)
                    st.success("Added!")
                    st.rerun()

    with tab_list:
        if config["sites"]:
            st.markdown("<b>Your Sites:</b>", unsafe_allow_html=True)
            for s in config["sites"]:
                st.text(f"- {s['name']}")
            
            st.markdown("---")
            del_site = st.selectbox("Delete Site", ["Select..."] + [s["name"] for s in config["sites"]])
            if del_site != "Select..." and st.button(f"Delete {del_site}"):
                config["sites"] = [s for s in config["sites"] if s["name"] != del_site]
                save_config(config)
                st.rerun()
        else:
            st.info("No sites added.")

    with tab_test:
        if config["sites"]:
            t_site_name = st.selectbox("Test Site", [s["name"] for s in config["sites"]])
            if st.button("Check Connection"):
                t_site = next((s for s in config["sites"] if s["name"] == t_site_name), None)
                if t_site:
                    with st.spinner("Connecting..."):
                        if not t_site['url'].endswith('/'): t_site['url'] += '/'
                        api = f"{t_site['url']}wp-json/wp/v2/users/me"
                        creds = f"{t_site['username'].strip()}:{t_site['password'].strip()}"
                        token = base64.b64encode(creds.encode()).decode('utf-8')
                        headers = {'Authorization': f'Basic {token}'}
                        try:
                            r = requests.get(api, headers=headers, timeout=10)
                            if r.status_code == 200:
                                st.success(f"OK! Connected as {r.json().get('name')}")
                            else:
                                st.error(f"Failed: {r.status_code}")
                        except Exception as e:
                            st.error(f"Error: {str(e)}")

    with st.expander("Model Settings", expanded=False):
        mod_name = st.text_input("Model ID", value=config.get("model", "gemini-2.0-flash-lite-preview-02-05"))
        if mod_name != config.get("model"):
            config["model"] = mod_name
            save_config(config)

# Main Area
col1, col2 = st.columns([3, 1])

# Session State
if "processing" not in st.session_state: st.session_state.processing = False
if "paused" not in st.session_state: st.session_state.paused = False
if "url_input" not in st.session_state: st.session_state.url_input = ""
if "logs" not in st.session_state: st.session_state.logs = []
if "results" not in st.session_state: st.session_state.results = []
if "current_url" not in st.session_state: st.session_state.current_url = ""
if "stop_requested" not in st.session_state: st.session_state.stop_requested = False

def add_log(msg):
    st.session_state.logs.append(msg)

def clear_all():
    st.session_state.url_input = ""
    st.session_state.logs = []
    st.session_state.results = []
    st.session_state.processing = False
    st.session_state.paused = False
    st.session_state.current_url = ""
    st.session_state.stop_requested = False

with col1:
    urls_input = st.text_area("URLs (one per line)", height=150, placeholder="https://site.com/post1...", key="url_input")
    
    # Control Buttons Row
    b_col1, b_col2, b_col3, b_col4 = st.columns(4)
    
    start_clicked = False
    
    with b_col1:
        if not st.session_state.processing:
            if st.button("Start Processing", type="primary", use_container_width=True):
                start_clicked = True
        else:
             st.button("Running...", disabled=True, use_container_width=True)

    with b_col2:
        if st.session_state.processing:
            if st.session_state.paused:
                if st.button("Resume", use_container_width=True):
                    st.session_state.paused = False
                    st.rerun()
            else:
                if st.button("Pause", use_container_width=True):
                    st.session_state.paused = True
                    st.rerun()
        else:
            st.button("Pause", disabled=True, use_container_width=True)
            
    with b_col3:
        if st.session_state.processing:
            if st.button("Stop", type="secondary", use_container_width=True):
                st.session_state.stop_requested = True
                st.session_state.processing = False
                st.rerun()
        else:
             st.button("Stop", disabled=True, use_container_width=True)

    with b_col4:
        st.button("Reset", on_click=clear_all, use_container_width=True, disabled=st.session_state.processing)

    # Status & Logs
    if st.session_state.processing and st.session_state.current_url:
        st.info(f"Processing: {st.session_state.current_url}")
    elif st.session_state.paused:
        st.warning("Paused.")
        
    # Log Box
    log_text = "\n".join([f">> {l}" for l in st.session_state.logs])
    st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)
    
    # Final Report
    if st.session_state.results and not st.session_state.processing:
        st.markdown("### Final Report")
        success_count = len([r for r in st.session_state.results if r['status'] == 'success'])
        fail_count = len([r for r in st.session_state.results if r['status'] == 'error'])
        st.markdown(f"**Completed: {success_count} | Failed: {fail_count}**")
        
        for res in st.session_state.results:
            if res["status"] == "success":
                st.success(f"✅ {res['url']} -> Post ID: {res['id']}")
            else:
                st.error(f"❌ {res['url']} -> {res['msg']}")

with col2:
    # Settings
    site_names = [s["name"] for s in config["sites"]]
    selected_site_name = st.selectbox("Target Site", site_names if site_names else ["No Sites Saved"])
    
    target_site = None
    selected_author_id = None
    selected_cat_ids = []

    if selected_site_name != "No Sites Saved":
        target_site = next((s for s in config["sites"] if s["name"] == selected_site_name), None)
        if target_site:
            # Auto fetch
            cats = get_wp_categories(target_site['url'], target_site['username'], target_site['password'])
            authors = get_wp_authors(target_site['url'], target_site['username'], target_site['password'])
            
            if cats:
                cat_names = list(cats.keys())
                sel_cat_name = st.selectbox("Category", cat_names)
                if sel_cat_name: selected_cat_ids = [cats[sel_cat_name]]
            
            if authors:
                auth_names = list(authors.keys())
                sel_auth_name = st.selectbox("Author", auth_names)
                if sel_auth_name: selected_author_id = authors[sel_auth_name]

    post_status = st.selectbox("Post Status", ["draft", "publish"])
    
    with st.expander("Options", expanded=True):
        opt_imgs = st.checkbox("Process Images", value=True)
        watermark_text = None
        wm_color = "white"
        wm_bg_color = None
        
        if opt_imgs:
            add_watermark = st.checkbox("Add Watermark")
            if add_watermark:
                watermark_text = st.text_input("Watermark Text")
                wm_color = st.color_picker("Text Color", "#FFFFFF")
                use_bg = st.checkbox("Text Background?")
                if use_bg:
                    wm_bg_color = st.color_picker("Bg Color", "#000000")
        
        opt_links = st.checkbox("Outbound Links", value=True)
        link_opts = {}
        if opt_links:
            link_opts['new_tab'] = st.checkbox("New Tab", value=True)
            link_opts['nofollow'] = st.checkbox("Nofollow", value=False)
            link_opts['sponsored'] = st.checkbox("Sponsored", value=False)

# --- Processing Logic ---
if start_clicked:
    if not config.get("api_key"):
        st.error("API Key missing!")
    elif not site_names or selected_site_name == "No Sites Saved":
        st.error("Select a site!")
    else:
        urls = [clean_url(u) for u in urls_input.split('\n') if u.strip()]
        if not urls:
            st.warning("No URLs!")
        else:
            st.session_state.processing = True
            st.session_state.paused = False
            st.session_state.stop_requested = False
            st.session_state.logs = []
            st.session_state.results = []
            st.rerun()

if st.session_state.processing and not st.session_state.paused:
    urls = [clean_url(u) for u in urls_input.split('\n') if u.strip()]
    
    # Determine next URL to process
    processed_urls = [r['url'] for r in st.session_state.results]
    remaining_urls = [u for u in urls if u not in processed_urls]
    
    if not remaining_urls:
        st.session_state.processing = False
        st.session_state.current_url = ""
        st.success("All Done!")
        st.rerun()
    else:
        current_url = remaining_urls[0]
        st.session_state.current_url = current_url
        
        # Prepare options
        options = {
            "imgs": opt_imgs, 
            "watermark_text": watermark_text,
            "wm_opts": {'color': wm_color, 'bg_color': wm_bg_color},
            "links": opt_links,
            "link_opts": link_opts, 
            "status": post_status,
            "author_id": selected_author_id,
            "category_ids": selected_cat_ids
        }
        
        # Log & Process
        add_log(f"Starting: {current_url}")
        st.rerun() 
        # Rerun to update UI with "Starting..." then actually process? 
        # No, Streamlit script runs top to bottom. If we rerun here, we loop.
        # We need to process *then* rerun or update. 
        # But we want to show "Starting..." first. 
        # Best way in this structure is: 
        # The UI above already shows `st.session_state.current_url`.
        # So we can just process now.
        
        # ACTUALLY, if we want dynamic updates (logs appearing line by line), 
        # we can't do full reruns for every log line in a single thread easily without blocking.
        # But user said "1 ta kore kaj korbe" (do 1 by 1). 
        # So we can run the WHOLE process for this URL here, then rerun for the next.
        
        # --- EXECUTION BLOCK ---
        try:
            # Scrape
            add_log("Scraping content...")
            html = scrape_content(current_url, options['imgs'], options['links'], options['link_opts'], 
                                  {'url': target_site['url'], 'user': target_site['username'], 'pass': target_site['password']},
                                  options['watermark_text'], options['wm_opts'])
            
            if html.startswith("Error"):
                add_log(f"Scrape Failed: {html}")
                st.session_state.results.append({"url": current_url, "status": "error", "msg": html})
            else:
                add_log("Scrape OK. Generating Rewrite...")
                rewritten = generate_rewrite(html, config["api_key"], config["model"])
                
                if not rewritten or rewritten.startswith("Error"):
                    add_log(f"Rewrite Failed: {rewritten}")
                    st.session_state.results.append({"url": current_url, "status": "error", "msg": rewritten})
                else:
                    add_log("Rewrite OK. Uploading...")
                    res = upload_to_wordpress(target_site['url'], target_site['username'], target_site['password'], 
                                              "AI Post", rewritten, options['status'], options['author_id'], options['category_ids'])
                    
                    if isinstance(res, dict) and 'id' in res:
                        add_log(f"Upload Success! ID: {res['id']}")
                        st.session_state.results.append({"url": current_url, "status": "success", "id": res['id']})
                    else:
                        add_log(f"Upload Failed: {res}")
                        st.session_state.results.append({"url": current_url, "status": "error", "msg": str(res)})
        
        except Exception as e:
            add_log(f"Exception: {str(e)}")
            st.session_state.results.append({"url": current_url, "status": "error", "msg": str(e)})
            
        # Move to next
        time.sleep(0.5)
        st.rerun()
