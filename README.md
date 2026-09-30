# Vuzïmo (voorheen Vitalized) feeds → Stock Sync

> **18-08-2026:** Vitalized heet Vuzïmo; `vitalized.com` en `partners.vuzimo.com`
> redirecten naar `vuzimo.com` / `partners.vuzimo.com`. De SKU's van de eigen lijn
> gingen van 400xx naar 401xx (oud + 100), met nieuwe EAN's. De repo houdt zijn naam.

Scrapt **Vuzïmo** (Shopware) en levert twee XML-feeds voor Stock Sync. Draait
automatisch via GitHub Actions; logt in op het partnerportaal met versleutelde
GitHub Secrets.

| Feed | Script | Output | Doel | Schema |
|---|---|---|---|---|
| **Update-feed** | `scraper.py` | `vitalized_feed.xml` | prijs + voorraad + inkoop van **bestaande** producten | 2×/dag (06:00 + 18:00 UTC) |
| **Add-feed** | `add_scraper.py` | `vitalized_add_feed.xml` | **nieuwe** producten aanmaken met álle info | 1×/week (ma 04:00 UTC) |

## Bron

Enumeratie + data via **partners.vuzimo.com** (Shopware, ingelogd) = het
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

## Vangnet tegen een lege feed

Beide scrapers roepen `controleer_omvang()` aan vóór het wegschrijven. Bij **0
producten**, of bij **minder dan de helft** van de vorige feed, stopt de run met een
foutcode: de oude feed blijft staan en de GitHub Action wordt rood. Zo kan een
verhuisd domein of een gewijzigde sitemap niet meer stil een lege feed opleveren
(dat gebeurde op 18-08-2026 en bleef 13 dagen onopgemerkt). Bewust doorzetten —
als de leverancier écht inkrimpt — kan met `FORCE_FEED=1`.

Keerzijde: een rode Action houdt de feed wel veilig, maar bevriest hem ook. Van
07-09 tot 29-09-2026 faalde elke run met `BadGzipFile`: de server stuurt de
`.xml.gz`-sitemaps sindsdien met `Content-Encoding: gzip`, requests pakt ze zelf al
uit en de scraper deed het daarna nog eens. Nu wordt alleen uitgepakt als de bytes
echt gzip zijn. Prijzen en voorraad stonden die drie weken stil in Stock Sync.

In dezelfde periode kwam er op de partnerpagina een verborgen blok "Short
expiration" bij (korte-THT-partij, −30%). Dat staat vóór de gewone partnerprijs; de
scraper nam het als inkoop, en bij producten zonder consumentenprijs zakte daardoor
ook de verkoopprijs 30% (Extraordinary Enzymes € 27,86 → € 19,50). Dat blok wordt
nu overgeslagen.

## Prijsvloer: de update-feed verlaagt nooit een verkoopprijs

Besluit Max, 30-09-2026. `prijsvloer()` in `scraper.py` houdt elke verkoopprijs op
minstens de prijs uit de vorige feed (= wat Stock Sync in de winkel zette). Verhogen
gaat gewoon door; de inkoopprijs volgt altijd de bron. Aanleiding: Vuzïmo en Life
Extension Europe verlaagden in september drie Quicksilver EU-producten met ~11%.

Een verlaging bewust doorlaten kan per SKU met de env-var `PRIJS_OMLAAG=<sku>,<sku>`
(of `PRIJS_OMLAAG=alle`). Let op: een foute, te hoge prijs blijft zo ook staan tot
hij op die manier wordt vrijgegeven. De add-feed heeft geen vloer; die zet alleen
prijzen van nieuwe producten.

## Automatische filters (uit de feed gelaten)

- Producten **zonder partnerprijs** (niet inkoopbaar).
- Producten die **niet naar Nederland** verzonden mogen worden
  ("cannot be shipped to following countries: Netherlands").

## Secrets (verplicht)

Zet in de repo onder **Settings → Secrets and variables → Actions**:

- `VITALIZED_USER` = je partner-login e-mail (`VUZIMO_USER` mag ook)
- `VITALIZED_PASS` = je partner-wachtwoord (`VUZIMO_PASS` mag ook)

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
