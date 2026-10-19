from datetime import date

import polars as pl
import pytest

from carburants import marches


def test_lire_fred_ignore_les_jours_sans_cotation():
    contenu = b"observation_date,DCOILBRENTEU\n2026-03-02,80.5\n2026-03-03,.\n2026-03-04,\n2026-03-05,82.0\n"
    serie = marches.lire_fred(contenu, "brent_usd_baril")
    assert serie["date"].to_list() == [date(2026, 3, 2), date(2026, 3, 5)]
    assert serie["brent_usd_baril"].to_list() == [80.5, 82.0]


def test_lire_fred_ancien_format():
    serie = marches.lire_fred(b"DATE,DEXUSEU\n2008-07-03,1.5788\n", "usd_par_eur")
    assert serie.row(0) == (date(2008, 7, 3), 1.5788)


def test_combiner_convertit_en_euros_par_litre():
    brent = pl.DataFrame({"date": [date(2026, 3, 2), date(2026, 3, 3)], "brent_usd_baril": [110.0, 111.0]})
    change = pl.DataFrame({"date": [date(2026, 3, 2)], "usd_par_eur": [1.10]})
    cours = marches.combiner(brent, change)
    assert cours.height == 1
    assert cours["brent_eur_litre"][0] == pytest.approx(110 / 1.10 / 158.987)


def test_lien_historique_bulletin():
    html = """
    <a href="/document/download/aaa_en?filename=Weekly_Oil_Bulletin_Taxes_History.xlsx">Taxes</a>
    <a href="/document/download/bbb_en?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx&amp;x=1">Prices</a>
    <a href="/document/download/ccc_en?filename=Prices_with_taxes_2026.xlsx">Latest</a>
    """
    lien = marches.lien_historique_bulletin(html)
    assert lien == (
        "https://energy.ec.europa.eu/document/download/bbb_en?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx&x=1"
    )


def test_lien_historique_bulletin_absent():
    with pytest.raises(ValueError, match="introuvable"):
        marches.lien_historique_bulletin('<a href="/autre.pdf">Rien</a>')
