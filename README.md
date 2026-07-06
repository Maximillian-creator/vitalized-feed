# Vitalized feeds → Stock Sync

Scrapt **Vitalized** (Shopware) en levert twee XML-feeds voor Stock Sync. Draait
automatisch via GitHub Actions; logt in op het partnerportaal met versleutelde
GitHub Secrets.

| Feed | Script | Output | Doel | Schema |
|---|---|---|---|---|
| **Update-feed** | `scraper.py` | `vitalized_feed.xml` | prijs + voorraad + inkoop van **bestaande** producten | 2×/dag (06:00 + 18:00 UTC) |
| **Add-feed** | `add_scraper.py` | `vitalized_add_feed.xml` | **nieuwe** producten aanmaken met álle info | 1×/week (ma 04:00 UTC) |

## Bron

Enumeratie + data via **partners.vitalized.com** (Shopware, ingelogd) = het
volledige inkoopbare assortiment (~366, alle merken). De partnerpagina levert
titel, merk, SKU, EAN, secties, afbeeldingen, **inkoop (partner price)** en
**echte voorraad**. (De partnerkorting op de inkoopprijs is alleen ingelogd zichtbaar.)

## Prijslogica

- `cost` = partner price (excl. BTW) → Shopify **"Kostprijs per artikel"**.
- `price` = **verkoopprijs uit inkoop via marge + BTW** (zelf-onderhoudend):
  `verkoop_excl = inkoop / (1 − MARGIN)`, `price = verkoop_excl × VAT_RATE`.
- Defaults: **MARGIN = 0,31** (31% brutomarge), **VAT_RATE = 1,09** (9% BTW).
  Instelbaar via env vars `MARGIN` / `VAT_RATE`.
- Gecontroleerd: inkoop 14,31 → 22,61, gelijk aan Vitalized's eigen consumentenprijs (22,50).

## Automatische filters (uit de feed gelaten)

- Producten **zonder partnerprijs** (niet inkoopbaar).
- Producten die **niet naar Nederland** verzonden mogen worden
  ("cannot be shipped to following countries: Netherlands").

## Secrets (verplicht)

Zet in de repo onder **Settings → Secrets and variables → Actions**:

- `VITALIZED_USER` = je partner-login e-mail
- `VITALIZED_PASS` = je partner-wachtwoord

Zonder deze secrets kan de Action niet inloggen en stopt hij met een duidelijke melding.

## Velden in de add-feed

Per `<product>`: `handle, title, vendor, sku, barcode, price, cost, available,
quantity, description`, losse secties (`ingredients`, `how_to_take`, …), een
`<images>`-blok en `image_links` (komma-gescheiden, voor Stock Sync).

## Stock Sync mapping

- **Add products** → feed-URL: `…/vitalized_add_feed.xml`. Map o.a. `sku` (identifier),
  `title`, `description`, `vendor`, `barcode`, `price`, `cost` (→ Kostprijs),
  `image_links` (scheidingsteken = komma), `quantity`.
- **Update** → feed-URL: `…/vitalized_feed.xml`. Match op `sku`; map `price`, `cost`,
  `quantity`.

## Lokaal draaien / testen

```bash
pip install -r requirements.txt
cp .env.example .env        # vul je login in (wordt niet gecommit)
python add_scraper.py                     # volledige add-feed
TEST_SLUG=vitamins-d-k-eu python add_scraper.py   # één product testen
INSECURE_SSL=1 python add_scraper.py      # achter een SSL-onderscheppende proxy
```
