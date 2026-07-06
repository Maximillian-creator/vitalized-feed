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
import collections
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
            "❌ Login-secrets ontbreken. Diagnose (waarden worden NIET getoond):\n"
            f"   VITALIZED_USER gezet: {bool(user)} (lengte {len(user or '')})\n"
            f"   VITALIZED_PASS gezet: {bool(pw)} (lengte {len(pw or '')})\n"
            "   Zet beide als Repository-secret onder Settings -> Secrets and "
            "variables -> Actions (tab 'Secrets'). Zie README."
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


def iter_product_slugs(base=PARTNER_BASE):
    """
    Haal alle product-slugs uit de sitemap. Standaard de PARTNERSITE
    (partners.vitalized.com) = het volledige inkoopbare assortiment (~366).
    Filtert grofweg content-pagina's eruit; de echte productcheck (JSON-LD)
    gebeurt tijdens het scrapen.
    """
    session = make_session()
    idx = fetch(session, f"{base}/sitemap.xml")
    if not idx:
        return []
    subs = re.findall(r"<loc>([^<]+)</loc>", idx.text)
    # alleen de hoofd-sitemap (niet de per-land varianten -mt-, -ie-)
    subs = [s for s in subs if re.search(r"-com-\d+\.xml", s)] or subs

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
            m = re.match(rf"{re.escape(base)}/([a-z0-9][a-z0-9-]*)/?$", loc)
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
# "Life Extension" en "Life Extension Europe" samenvoegen tot één merk
BRAND_ALIASES = {
    "life extension europe": "Life Extension",
}


def normalize_brand(brand):
    if not brand:
        return ""
    return BRAND_ALIASES.get(brand.strip().lower(), brand.strip())


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


def parse_product(html):
    """
    Rijke productinfo uit een Shopware-productpagina (werkt op beide sites).
    `price` = de JSON-LD offer-prijs OP DIE PAGINA:
      - op de partnerpagina  = partner price (inkoop, excl. BTW)
      - op de consumentensite = consumentenprijs (verkoop, incl. BTW)
    """
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
        "brand": normalize_brand(brand),
        "ean": extract_ean(html),
        "price": price,
        "availability": (offer.get("availability") or "").split("/")[-1],
        "description": clean_text(ld.get("description") or ""),
        "sections": extract_sections(html),
        "images": extract_images(html, ld.get("sku") or ld.get("mpn")),
    }


def parse_stock_shipping(html):
    """Echte voorraad + NL-verzendbeperking van de partnerpagina."""
    stock = None
    m = re.search(r"([0-9][0-9.\s]*)\s*in\s*stock", html, re.IGNORECASE)
    if m:
        try:
            stock = int(re.sub(r"[^\d]", "", m.group(1)))
        except ValueError:
            stock = None
    available = bool(stock) or "in stock" in html.lower()

    # "This product cannot be shipped to following countries: ... Netherlands ..."
    block = re.search(
        r"cannot be shipped to (?:the )?following countries[:\s]*([^<]{0,300})",
        html, re.IGNORECASE,
    )
    nl_blocked = bool(block and re.search(r"netherland|nederland", block.group(1), re.IGNORECASE))
    return {"stock": stock, "available": available, "nl_blocked": nl_blocked}


# Prijsberekening: verkoopprijs uit inkoop via marge + BTW.
#   marge = brutomarge op verkoop excl. BTW  ->  verkoop_excl = inkoop / (1 - marge)
#   verkoop_incl = verkoop_excl × BTW-factor
# Gecontroleerd: inkoop 14,31 / 0,69 × 1,09 = 22,60 ≈ Vitalized's eigen prijs 22,50.
# Instelbaar via env (MARGIN / VAT_RATE); default 31% marge en 9% BTW (supplementen).
MARGIN = float(os.environ.get("MARGIN", "0.31"))
VAT_RATE = float(os.environ.get("VAT_RATE", "1.09"))


def selling_price(cost):
    if cost is None:
        return None
    return round(cost / (1 - MARGIN) * VAT_RATE, 2)


def scrape_products(session, slugs):
    """
    Enumereer het PARTNER-assortiment (ingelogd). Per product levert de
    partnerpagina: titel/EAN/merk/info/afbeeldingen + inkoop (partner price) +
    voorraad. Verkoopprijs = inkoop via marge + BTW (selling_price).
    Slaat over: geen inkoopprijs (niet inkoopbaar) en niet-NL-leverbaar.
    """
    total = len(slugs)
    skipped_nonproduct = 0
    skipped_no_cost = []
    skipped_nl_blocked = []

    # Testmodus: alleen N producten per opgegeven merk (substring-match, bv.
    # "quicksilver scientific" vangt ook "...europe"). TEST_BRANDS leeg = alles.
    test_brands = [b.strip().lower() for b in os.environ.get("TEST_BRANDS", "").split(",") if b.strip()]
    per_brand = int(os.environ.get("TEST_PER_BRAND", "7") or 7)
    got = collections.Counter()

    def wanted_brand(brand):
        bl = (brand or "").lower()
        for w in test_brands:
            if w in bl:
                return w
        return None

    for i, slug in enumerate(slugs, 1):
        if test_brands and all(got[w] >= per_brand for w in test_brands):
            print("🧪 Testmodus: alle merk-quota gehaald, stoppen.")
            break
        phtml = fetch(session, f"{PARTNER_BASE}/{slug}", allow_404=True)
        if not phtml:
            continue
        prod = parse_product(phtml.text)          # price hier = INKOOP
        if not prod["title"]:
            skipped_nonproduct += 1
            continue

        cost = prod["price"]
        ship = parse_stock_shipping(phtml.text)

        if cost is None:
            skipped_no_cost.append(prod["title"])
            print(f"  [{i}/{total}] {prod['title'][:48]:48} ⏭️  geen inkoopprijs")
            time.sleep(REQUEST_DELAY)
            continue
        if ship["nl_blocked"]:
            skipped_nl_blocked.append(prod["title"])
            print(f"  [{i}/{total}] {prod['title'][:48]:48} 🚫 niet naar NL")
            time.sleep(REQUEST_DELAY)
            continue

        if test_brands:
            w = wanted_brand(prod["brand"])
            if not w or got[w] >= per_brand:
                time.sleep(REQUEST_DELAY)
                continue
            got[w] += 1

        price = selling_price(cost)
        print(f"  [{i}/{total}] {prod['title'][:48]:48} €{price} (inkoop €{cost}, {ship['stock']} vrd)")

        prod.update(ship)
        prod["cost"] = cost
        prod["price"] = price
        prod["slug"] = slug
        yield prod
        time.sleep(REQUEST_DELAY)

    print(f"\nℹ️  {total - skipped_nonproduct - len(skipped_no_cost) - len(skipped_nl_blocked)} in feed | "
          f"overgeslagen: {skipped_nonproduct} niet-producten, "
          f"{len(skipped_no_cost)} zonder inkoopprijs, "
          f"{len(skipped_nl_blocked)} niet-NL "
          f"(marge {MARGIN:.0%}, BTW ×{VAT_RATE}).")
    if skipped_nl_blocked:
        print("🚫 Niet naar NL (uit feed gelaten):")
        for t in skipped_nl_blocked:
            print(f"     - {t}")
