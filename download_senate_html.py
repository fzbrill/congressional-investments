"""
download_senate_html.py  — fetch all 93 Senate annual disclosure HTML pages.
Run once from your terminal:  python download_senate_html.py

The script agrees to the eFD terms, then saves each senator's annual report
as an HTML file in Downloads/senate_html_2024/.
"""
import urllib.request, urllib.parse, http.cookiejar, os, time, csv, re

TSV  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'senate_members_2024.tsv')
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'senate_html_2024')
os.makedirs(DEST, exist_ok=True)

# Build a session with cookie support
jar     = http.cookiejar.CookieJar()
opener  = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [('User-Agent', 'Mozilla/5.0 (public interest research)')]

def get(url):
    return opener.open(url, timeout=15)

def post(url, data, referer=None):
    headers = [('Referer', referer or url)] if referer else []
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), headers=dict(headers))
    return opener.open(req, timeout=15)

# Step 1 — agree to terms (sets session cookie)
home_resp = get('https://efdsearch.senate.gov/search/home/')
home_html = home_resp.read().decode('utf-8')
csrf_m    = re.search(r'csrfmiddlewaretoken[^>]+value="([^"]+)"', home_html)
token     = csrf_m.group(1) if csrf_m else ''
post('https://efdsearch.senate.gov/search/home/',
     {'csrfmiddlewaretoken': token, 'prohibition_agreement': '1'},
     referer='https://efdsearch.senate.gov/search/home/')
print("Session established.")

# Step 2 — download each report page
with open(TSV, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f, delimiter='\t'))

total = len(rows)
for i, row in enumerate(rows, 1):
    name = row['NAME'].strip()
    url  = row['URL'].strip()
    uuid = url.rstrip('/').split('/')[-1]
    dest = os.path.join(DEST, f'{uuid}.html')

    if os.path.exists(dest) and os.path.getsize(dest) > 2000:
        print(f'[{i}/{total}] skip  {name}')
        continue

    try:
        resp = get(url)
        html = resp.read().decode('utf-8')

        # If session expired, re-agree and retry once
        if 'Annual Report' not in html and 'Part 3' not in html:
            print(f'[{i}/{total}] re-auth {name}')
            home2    = get('https://efdsearch.senate.gov/search/home/')
            home2_html = home2.read().decode('utf-8')
            csrf2_m  = re.search(r'csrfmiddlewaretoken[^>]+value="([^"]+)"', home2_html)
            if csrf2_m:
                post('https://efdsearch.senate.gov/search/home/',
                     {'csrfmiddlewaretoken': csrf2_m.group(1), 'prohibition_agreement': '1'},
                     referer='https://efdsearch.senate.gov/search/home/')
            resp = get(url)
            html = resp.read().decode('utf-8')

        with open(dest, 'w', encoding='utf-8') as out:
            out.write(html)
        print(f'[{i}/{total}] ok    {name}  ({len(html)//1024}KB)')
    except Exception as e:
        print(f'[{i}/{total}] ERROR {name}: {e}')

    time.sleep(0.4)

print(f'\nDone. HTML files in: {DEST}')
