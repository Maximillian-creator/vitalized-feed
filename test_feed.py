"""Tests zonder netwerk: wat een Vuzimo-partnerpagina betekent voor 'beschikbaar'.

    python -m pytest -q test_feed.py
"""
import vitalized_common as vc

# de verborgen 'houd mij op de hoogte'-tekst staat op elke pagina (ook op leverbare)
MELDING = ("data-notification-form-options='{\"snippets\": {\"successMessage\": "
           "\"Done. You will be notified when the product is in stock again!\"}}'")
UITVERKOCHT = (MELDING + '<div class="out-of-stock-container col-lg-12"> Out of stock </div>'
               '<script type="application/ld+json">{"offers": {"availability": "https:\\/\\/schema.org\\/SoldOut"}}</script>')
LEVERBAAR = (MELDING + '<span>24 in stock</span>'
             '<script type="application/ld+json">{"offers": {"availability": "https://schema.org/InStock"}}</script>')


def test_uitverkocht_is_niet_beschikbaar_ondanks_de_meldingstekst():
    assert vc.parse_stock_shipping(UITVERKOCHT)["available"] is False      # Enhanced Zinc Lozenges, 05-10


def test_leverbaar_blijft_beschikbaar():
    s = vc.parse_stock_shipping(LEVERBAAR)
    assert s["available"] is True and s["stock"] == 24


def test_zonder_ld_alleen_een_getal_telt():
    assert vc.parse_stock_shipping(MELDING + "<span>3 in stock</span>")["available"] is True
    assert vc.parse_stock_shipping(MELDING)["available"] is False
    assert vc.parse_stock_shipping(MELDING + '<span>3 in stock</span><div class="out-of-stock-container">')["available"] is False
