import httpx
import re

url = 'https://portal.fahrschulmanager.de/main.8ec66026204cb9fa.js'
print(f'Downloading {url}...')
r = httpx.get(url, timeout=30.0)
text = r.text

# Find all occurrences of http.(get|post|put|delete|patch)(...)
http_calls = re.findall(r'\.http\.(get|post|put|delete|patch)[<a-zA-Z0-9_>]*\(([^,\)]+)', text)
print(f'Found {len(http_calls)} Angular HTTP client calls.')

endpoints = set()
for method, arg in http_calls:
    arg = arg.strip()
    # Extract string literals in argument
    literals = re.findall(r'["\'`]([^"\'`]+)["\'`]', arg)
    for lit in literals:
        if any(c in lit for c in ['v1', 'v2', 'v3', 'api', '/', 'schueler', 'lehrer', 'kalender', 'kassen', 'leistung', 'kurs', 'fahrstunde', 'theorie', 'fahrzeug']):
            endpoints.add((method.upper(), lit))

print(f'\n=== EXTRACTED {len(endpoints)} HTTP SERVICE ENDPOINTS ===\n')
for method, ep in sorted(endpoints):
    print(f'{method:6} {ep}')

# Also search for all URL fragments containing common API resources
resources = ['schueler', 'lehrer', 'fahrlehrer', 'termine', 'kalender', 'kassenbuecher', 'fahrzeug', 'filialen', 'klassen', 'leistungen', 'theorie', 'theoriestunden', 'theoriekapitel', 'kurse', 'teilnehmer', 'eingangsrechnung', 'rechnungen', 'statistiken', 'preise', 'preislisten', 'ausbildungen', 'antrag', 'gutscheine', 'vertraege', 'sms', 'onlineanmeldung', 'dokumente', 'nachrichten', 'export', 'import', 'benutzer', 'rechte', 'einstellungen']

found_paths = set()
for res in resources:
    matches = re.findall(rf'["\'`](/?(?:v1|v2|v3)?/?{res}[a-zA-Z0-9_\-\/{{}}\$]*)["\'`]', text, re.IGNORECASE)
    for m in matches:
        if not m.endswith('.js') and not m.endswith('.html') and not m.endswith('.css') and not m.endswith('.svg') and not m.endswith('.png') and len(m) > 2:
            found_paths.add(m)

print(f'\n=== DISCOVERED {len(found_paths)} RESOURCE URL PATTERNS ===\n')
for p in sorted(found_paths):
    print(f'   • {p}')

