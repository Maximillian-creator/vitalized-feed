"""
Vitalized ADD-feed
==================
Volledige productinfo om met Stock Sync NIEUWE producten aan te maken.
Bron: vitalized.com (verkoopprijs + info) + partners.vitalized.com (inkoop + voorraad).

  price  = consumentenprijs (incl. BTW)          -> Shopify verkoopprijs
  cost   = partner price (excl. BTW)              -> Shopify "kostprijs per artikel"
  stock  = echte voorraad van de partnerportal

Producten die niet naar Nederland verzonden mogen worden, of geen partnerprijs
hebben, worden overgeslagen (zie vitalized_common.scrape_products).

Env: VITALIZED_USER, VITALIZED_PASS (GitHub Secrets). Lokaal: INSECURE_SSL=1,
TEST_SLUG=<slug> om één product te testen.
"""

import os
import time
import xml.etree.ElementTree as ET
from xml.dom import minidom
from html import escape

import vitalized_common as vc

OUTPUT_FILE = "vitalized_add_feed.xml"


def build_description_html(prod):
    """body (JSON-LD description) + secties tot één HTML-beschrijving."""
    parts = []
    if prod.get("description"):
        parts.append(f"<p>{escape(prod['description'])}</p>")
    for heading in vc.SECTION_HEADINGS:
        val = prod["sections"].get(heading)
        if val:
            parts.append(f"<p><strong>{escape(heading)}:</strong> {escape(val)}</p>")
    return "\n".join(parts)


def add_child(parent, tag, value):
    el = ET.SubElement(parent, tag)
    el.text = "" if value is None else str(value)
    return el


def build_xml(products):
    root = ET.Element("products")
    for p in products:
        item = ET.SubElement(root, "product")
        add_child(item, "handle", p["slug"])
        add_child(item, "title", p["title"])
        add_child(item, "vendor", p["brand"])
        add_child(item, "sku", p["sku"])
        add_child(item, "barcode", p["ean"])
        add_child(item, "price", f"{p['price']:.2f}")          # verkoop, incl. BTW
        add_child(item, "cost", f"{p['cost']:.2f}")            # inkoop, excl. BTW
        add_child(item, "available", "true" if p["available"] else "false")
        add_child(item, "quantity", p["stock"] if p["stock"] is not None else "")
        add_child(item, "description", build_description_html(p))
        # losse secties (handig als aparte metafields)
        for heading in vc.SECTION_HEADINGS:
            tag = heading.lower().replace(" ", "_")
            add_child(item, tag, p["sections"].get(heading, ""))
        # afbeeldingen
        images_el = ET.SubElement(item, "images")
        for src in p["images"]:
            img = ET.SubElement(images_el, "image")
            add_child(img, "src", src)
        add_child(item, "image_links", ",".join(p["images"]))
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
    print("🚀 Vitalized ADD-feed gestart\n")
    start = time.time()

    session = vc.make_session()
    vc.login(session)

    test_slug = os.environ.get("TEST_SLUG")
    slugs = [test_slug] if test_slug else vc.iter_product_slugs()
    print(f"📦 {len(slugs)} slug(s) te verwerken\n")

    products = list(vc.scrape_products(session, slugs))
    root = build_xml(products)
    save_xml(root, OUTPUT_FILE)

    print(f"⏱️  Klaar in {time.time() - start:.0f}s — {len(products)} producten in de feed")
    print("\n📋 Feed-URL voor Stock Sync (Add products):")
    print("https://raw.githubusercontent.com/Maximillian-creator/vitalized-feed/main/vitalized_add_feed.xml")


if __name__ == "__main__":
    main()
