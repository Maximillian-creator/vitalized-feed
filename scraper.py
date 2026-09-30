"""
Vitalized UPDATE-feed
=====================
Lichte feed om BESTAANDE producten bij te werken: verkoopprijs, inkoopprijs,
voorraad en beschikbaarheid. Matcht in Stock Sync op SKU.

  price = consumentenprijs (incl. BTW)   -> Shopify verkoopprijs
  cost  = partner price (excl. BTW)       -> Shopify "kostprijs per artikel"
  quantity = echte voorraad partnerportal

Zelfde bronnen/filters als de add-feed (o.a. niet-NL-leverbaar wordt overgeslagen).
De verkoopprijs gaat nooit omlaag (zie prijsvloer); vrijgeven met PRIJS_OMLAAG.
Env: VITALIZED_USER, VITALIZED_PASS. Lokaal: INSECURE_SSL=1, TEST_SLUG=<slug>.
"""

import os
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom

import vitalized_common as vc

OUTPUT_FILE = "vitalized_feed.xml"


def prijsvloer(products, filepath):
    """
    De feed verlaagt nooit een verkoopprijs (besluit Max, 30-09-2026). Vloer = de
    prijs in de vorige feed, want die heeft Stock Sync in de winkel gezet.
    Verhogen gaat gewoon door; de inkoopprijs volgt altijd de bron.
    Vrijgeven per SKU met PRIJS_OMLAAG=<sku>,<sku> (of PRIJS_OMLAAG=alle).
    Keerzijde: een foute te hoge prijs blijft staan tot hij zo wordt vrijgegeven.
    """
    vrij = {s.strip() for s in os.environ.get("PRIJS_OMLAAG", "").split(",") if s.strip()}
    if "alle" in vrij or not os.path.exists(filepath):
        return
    vorige = {}
    for p in ET.parse(filepath).getroot().findall("product"):
        try:
            vorige[(p.findtext("sku") or "").strip()] = float(p.findtext("price") or "")
        except ValueError:
            continue
    tegengehouden = 0
    for p in products:
        oud = vorige.get(p["sku"])
        if oud is None or p["price"] is None or p["sku"] in vrij:
            continue
        if p["price"] < oud:
            print(f"  ⛔ {p['sku']:10} {p['title'][:40]:40} bron €{p['price']} → blijft €{oud}")
            p["price"] = oud
            tegengehouden += 1
    print(f"🔒 Prijsvloer: {tegengehouden} verlaging(en) tegengehouden")


def build_xml(products):
    root = ET.Element("products")
    for p in products:
        item = ET.SubElement(root, "product")
        def add(tag, value):
            el = ET.SubElement(item, tag)
            el.text = "" if value is None else str(value)
        add("sku", p["sku"])
        add("barcode", p["ean"])
        add("price", f"{p['price']:.2f}" if p["price"] is not None else "")
        add("cost", f"{p['cost']:.2f}")
        add("available", "true" if p["available"] else "false")
        add("quantity", p["stock"] if p["stock"] is not None else "")
    return root


def save_xml(root, filepath):
    xml_str = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n💾 XML opgeslagen: {filepath}")


def main():
    print("🚀 Vitalized UPDATE-feed gestart\n")
    start = time.time()

    session = vc.make_session()  # geen login nodig: partnerprijzen zijn openbaar (bruto)

    test_slug = os.environ.get("TEST_SLUG")
    slugs = [test_slug] if test_slug else vc.iter_product_slugs()
    print(f"📦 {len(slugs)} slug(s) te verwerken\n")

    products = list(vc.scrape_products(session, slugs))
    prijsvloer(products, OUTPUT_FILE)
    root = build_xml(products)
    vc.controleer_omvang(len(root.findall("product")), OUTPUT_FILE)
    save_xml(root, OUTPUT_FILE)

    print(f"⏱️  Klaar in {time.time() - start:.0f}s — {len(products)} producten in de feed")
    print("\n📋 Feed-URL voor Stock Sync (Update):")
    print("https://raw.githubusercontent.com/Maximillian-creator/vitalized-feed/main/vitalized_feed.xml")


if __name__ == "__main__":
    main()
