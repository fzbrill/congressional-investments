import urllib.request, yaml

url = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main/legislators-current.yaml"
data = urllib.request.urlopen(url, timeout=15).read()
legs = yaml.safe_load(data)
for leg in legs:
    bg = leg.get('id', {}).get('bioguide')
    if bg in ('C001098', 'M000355', 'B001135', 'S000148'):  # Cruz, McConnell, Budd, Schumer
        n = leg.get('name', {})
        print(f"{bg}: first={n.get('first')!r} nickname={n.get('nickname')!r} official_full={n.get('official_full')!r}")
