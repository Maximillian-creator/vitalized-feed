"""
Vitalized — gedeelde scraper-kern
=================================
Twee bronnen, gematcht op product-slug (en EAN als controle):

  1. vitalized.com            (OPENBAAR, Shopware) → verkoopprijs (incl. BTW) +
                               alle productinfo: titel, merk, SKU, EAN, secties,
                               afbeeldingen. Hier enumereren we ook via de sitemap.
  2. partners.vitalized.com   (LOGIN, Shopware)    → inkoopprijs (partner price,
                               excl. BTW) + echte voorraad.

Prijslogica (afgesproken):
  - price = consumentenprijs van vitalized.com, INCL. BTW, 1-op-1 (geen opslag).
  - cost  = partner price van de partnerportal (excl. BTW) → Shopify "kostprijs".
  - Een product zonder partnerpagina/partnerprijs kun je niet inkopen → overslaan.

Login gebruikt jouw partner-inlog uit env vars (in GitHub Actions: Secrets):
  VITALIZED_USER, VITALIZED_PASS
Lokaal achter een SSL-onderscheppende proxy: zet INSECURE_SSL=1.
"""

import os
import re
import gzip
import json
import time
from html import unescape

import requests

PARTNER_BASE = "https://partners.vitalized.com"
CONSUMER_BASE = "https://vitalized.com"
REQUEST_DELAY = 0.6

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GoodForYouFeedBot/1.0; +https://goodforyouonline.nl)",
    "Accept-Language": "en;q=0.9,nl;q=0.8",
}

VERIFY_SSL = os.environ.get("INSECURE_SSL") != "1"
if not VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings()

# Accordeon-secties die we (best-effort) van de productpagina plukken
SECTION_HEADINGS = [
    "Health benefits overview",
    "Why this formula works",
    "Backed by science",
    "How to take",
    "Ingredients",
]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    s.verify = VERIFY_SSL
    return s


def fetch(session, url, retries=3, allow_404=False):
    """GET met retry. Bij 404 (en allow_404) → None teruggeven zonder herhalen."""
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404 and allow_404:
                return None
            r.raise_for_status()
            return r
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404 and allow_404:
                return None
            if attempt < retries - 1:
                wait = (attempt + 1) * 20
                print(f"    ⚠️  Fout ({e}) bij {url} — opnieuw in {wait}s...")
                time.sleep(wait)
            else:
                print(f"    ❌ Mislukt na {retries} pogingen: {url} ({e})")
                return None


def login(session):
    """
    Log in op het partnerportaal (Shopware storefront).
    Flow: GET /account/login (sessie-cookie) → POST username/password.
    Geeft True bij succes. Geen 2FA/CSRF op dit formulier (gecontroleerd).
    """
    user = os.environ.get("VITALIZED_USER")
    pw = os.environ.get("VITALIZED_PASS")
    if not user or not pw:
        raise SystemExit(
            "❌ VITALIZED_USER / VITALIZED_PASS ontbreken. Zet ze als GitHub Secrets "
            "(of env vars lokaal). Zie README."
        )

    login_url = f"{PARTNER_BASE}/account/login"
    fetch(session, login_url)  # sessie-cookie ophalen

    resp = session.post(
        login_url,
        data={
            "username": user,
            "password": pw,
            "redirectTo": "frontend.account.home.page",
        },
        timeout=20,
        allow_redirects=True,
    )

    # Verificatie: de accountpagina mag niet terugsturen naar /login
    check = session.get(f"{PARTNER_BASE}/account", timeout=20, allow_redirects=True)
    ok = "/account/login" not in check.url and resp.status_code < 400
    if ok:
        print("🔐 Ingelogd op partnerportaal.")
    else:
        raise SystemExit(
            "❌ Inloggen mislukt. Controleer VITALIZED_USER/VITALIZED_PASS. "
            f"(eindigde op {check.url})"
        )
    return ok


# --------------------------------------------------------------------------- #
# Enumeratie via sitemap (openbare consumentensite)
# --------------------------------------------------------------------------- #
# Niet-product-pagina's die ook in de sitemap staan
NON_PRODUCT_SLUGS = {
    "delivery-payment", "faq", "about-us", "contact", "contact-us", "terms",
    "privacy", "imprint", "returns", "shipping", "blog", "homepage", "account",
    "wishlist", "search", "sale", "newsletter", "cookie-policy",
}


def iter_product_slugs():
    """
    Haal alle product-slugs uit de sitemap van vitalized.com.
    Filtert grofweg content-pagina's eruit; de echte productcheck (JSON-LD)
    gebeurt tijdens het scrapen.
    """
    session = make_session()
    idx = fetch(session, f"{CONSUMER_BASE}/sitemap.xml")
    if not idx:
        return []
    subs = re.findall(r"<loc>([^<]+)</loc>", idx.text)
    # alleen de hoofd-sitemap (niet de per-land varianten -mt-, -ie-)
    subs = [s for s in subs if re.search(r"-vitalized-com-\d+\.xml", s)] or subs

    slugs = []
    seen = set()
    for sub in subs:
        raw = fetch(session, sub)
        if not raw:
            continue
        content = raw.content
        if sub.endswith(".gz"):
            content = gzip.decompress(content)
        xml = content.decode("utf-8", "ignore")
        for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
            m = re.match(rf"{re.escape(CONSUMER_BASE)}/([a-z0-9][a-z0-9-]*)/?$", loc)
            if not m:
                continue
            slug = m.group(1)
            if slug in NON_PRODUCT_SLUGS or slug in seen:
                continue
            seen.add(slug)
            slugs.append(slug)
        time.sleep(REQUEST_DELAY)
    return slugs


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def clean_text(fragment):
    if not fragment:
        return ""
    t = re.sub(r"<[^>]+>", " ", fragment)
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _find_product_ld(html):
    for block in re.findall(
        r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL
    ):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue

        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") == "Product":
                    return node
                stack.extend(node.values())
    return None


def extract_ean(html):
    m = re.search(r"(?:ean|gtin1?3?)[^0-9]{0,12}(\d{12,14})", html, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_images(html, sku=None):
    """
    Product-afbeeldingen (Shopware media op b-cdn), lazy-load via data-src.
    De bestandsnaam begint met de SKU (bv. 02040eu_...), dus daarop filteren we
    om foto's van aanbevolen producten uit te sluiten.
    """
    urls = []
    for m in re.findall(r'(?:data-src|src|content)="([^"]*?/media/[^"]+?\.(?:png|jpe?g|webp))', html, re.IGNORECASE):
        u = unescape(m).split("?")[0]
        u = re.sub(r"(?<!:)//media/", "/media/", u)  # dubbele slash normaliseren
        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("/media/"):
            # relatief → host onbekend; sla over (we hebben liever absolute)
            continue
        if "logo" in u.lower() or "icon" in u.lower():
            continue
        if u not in urls:
            urls.append(u)

    if sku:
        matched = [u for u in urls if sku.lower() in u.rsplit("/", 1)[-1].lower()]
        if matched:
            return matched
    return urls[:6]  # veilige fallback als de SKU niet in de bestandsnaam zit


def extract_sections(html):
    """
    Best-effort: pak per bekende kop de tekst tot de volgende kop.
    Shopware-accordeons variëren; dit vangt de meeste 'Ingredients' e.d.
    """
    result = {}
    for heading in SECTION_HEADINGS:
        m = re.search(
            rf'>{re.escape(heading)}\s*<[^>]*>(.*?)(?=<(?:h[1-6]|button|summary|/div>\s*</div>\s*</div>))',
            html, re.DOTALL | re.IGNORECASE,
        )
        if m:
            txt = clean_text(m.group(1))
            if txt and len(txt) > 2:
                result[heading] = txt
    return result


def parse_consumer(html):
    """Verkoopprijs + alle rijke productinfo van vitalized.com."""
    ld = _find_product_ld(html) or {}
    offers = ld.get("offers")
    offer = offers[0] if isinstance(offers, list) else (offers or {})
    brand = ld.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")

    price = offer.get("price")
    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None

    return {
        "title": ld.get("name") or "",
        "sku": ld.get("sku") or ld.get("mpn") or "",
        "brand": brand or "",
        "ean": extract_ean(html),
        "price": price,                         # consumentenprijs, incl. BTW
        "availability": (offer.get("availability") or "").split("/")[-1],
        "description": clean_text(ld.get("description") or ""),
        "sections": extract_sections(html),
        "images": extract_images(html, ld.get("sku") or ld.get("mpn")),
    }


def parse_partner(html):
    """Inkoopprijs (partner price, excl. BTW) + echte voorraad."""
    # Partnerprijs: JSON-LD offer.price op de partnerpagina
    ld = _find_product_ld(html) or {}
    offers = ld.get("offers")
    offer = offers[0] if isinstance(offers, list) else (offers or {})
    cost = offer.get("price")
    try:
        cost = round(float(cost), 2) if cost is not None else None
    except (TypeError, ValueError):
        cost = None

    # Voorraad: "7098 in Stock"
    stock = None
    m = re.search(r"([0-9][0-9.\s]*)\s*in\s*stock", html, re.IGNORECASE)
    if m:
        try:
            stock = int(re.sub(r"[^\d]", "", m.group(1)))
        except ValueError:
            stock = None
    available = bool(stock) or "in stock" in html.lower()

    # Verzendbeperking: sommige producten mogen NIET naar Nederland.
    # "This product cannot be shipped to following countries: ... Netherlands ..."
    block = re.search(
        r"cannot be shipped to (?:the )?following countries[:\s]*([^<]{0,300})",
        html, re.IGNORECASE,
    )
    nl_blocked = bool(block and re.search(r"netherland|nederland", block.group(1), re.IGNORECASE))

    return {"cost": cost, "stock": stock, "available": available, "nl_blocked": nl_blocked}


def scrape_products(session, slugs, need_login=True):
    """
    Loop over slugs; combineer consumenten- en partnerdata.
    Yield dicts met alle velden. Slaat producten zonder partnerprijs over
    (niet inkoopbaar).
    """
    total = len(slugs)
    skipped_nonproduct = 0
    skipped_nonpartner = []
    skipped_nl_blocked = []

    for i, slug in enumerate(slugs, 1):
        chtml = fetch(session, f"{CONSUMER_BASE}/{slug}", allow_404=True)
        if not chtml:
            continue
        cons = parse_consumer(chtml.text)
        if not cons["title"] or cons["price"] is None:
            skipped_nonproduct += 1
            continue

        # Partnerdata (inkoop + voorraad + verzendbeperking)
        phtml = fetch(session, f"{PARTNER_BASE}/{slug}", allow_404=True)
        part = parse_partner(phtml.text) if phtml else {
            "cost": None, "stock": None, "available": False, "nl_blocked": False
        }

        if part["cost"] is None:
            skipped_nonpartner.append(cons["title"])
            print(f"  [{i}/{total}] {cons['title'][:50]:50} ⏭️  geen partnerprijs")
            time.sleep(REQUEST_DELAY)
            continue

        if part.get("nl_blocked"):
            skipped_nl_blocked.append(cons["title"])
            print(f"  [{i}/{total}] {cons['title'][:50]:50} 🚫 niet naar NL — overgeslagen")
            time.sleep(REQUEST_DELAY)
            continue

        print(f"  [{i}/{total}] {cons['title'][:50]:50} €{cons['price']} (inkoop €{part['cost']}, {part['stock']} op voorraad)")
        yield {**cons, **part, "slug": slug}
        time.sleep(REQUEST_DELAY)

    print(f"\nℹ️  Overgeslagen: {skipped_nonproduct} niet-producten, "
          f"{len(skipped_nonpartner)} zonder partnerprijs, "
          f"{len(skipped_nl_blocked)} niet-leverbaar naar NL.")
    if skipped_nl_blocked:
        print("🚫 Niet naar NL (uit feed gelaten):")
        for t in skipped_nl_blocked:
            print(f"     - {t}")
