import io
from datetime import date, datetime

import pytest

from carburants import marches

openpyxl = pytest.importorskip("openpyxl")


def _classeur(feuilles):
    classeur = openpyxl.Workbook()
    classeur.remove(classeur.active)
    for nom, lignes in feuilles.items():
        feuille = classeur.create_sheet(nom)
        for ligne in lignes:
            feuille.append(ligne)
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


def _feuille(titre, code, valeurs):
    entetes = [titre, "CTR", f"{code}_euro95", f"{code}_diesel", f"{code}_LPG", "CTR", "DE_x_euro95"]
    lignes = [entetes, ["", "", "Euro-super 95", "Gas oil", "GPL", "", ""], ["Date", "", "1000 l", "1000 l", "1000 l", "", ""]]
    for jour, sp95, gazole in valeurs:
        lignes.append([jour, "FR", sp95, gazole, 700.0, "DE", 1.0])
    return lignes


def test_lire_bulletin_france_en_euros_par_litre():
    semaines = [(datetime(2026, 10, 12), 1153.3, 1332.7), (datetime(2026, 10, 5), "n.a.", 1355.4)]
    contenu = _classeur(
        {
            "Prices with taxes": _feuille("Consumer prices inclusive of taxes", "FR_price_with_tax", [(d, 2212.2, 2328.3) for d, _, _ in semaines]),
            "Prices wo taxes": _feuille("Consumer prices net of taxes", "FR_price_wo_tax", semaines),
        }
    )
    bulletin = marches.lire_bulletin(contenu)
    assert bulletin.columns == ["date", "sp95_ht", "sp95_ttc", "gazole_ht", "gazole_ttc"]
    assert bulletin["date"].to_list() == [date(2026, 10, 5), date(2026, 10, 12)]
    assert bulletin["sp95_ht"].to_list() == [None, pytest.approx(1.1533)]
    assert bulletin["gazole_ttc"].to_list() == [pytest.approx(2.3283)] * 2


def test_lire_bulletin_pays_absent():
    contenu = _classeur({"Prices with taxes": [["titre"]], "Prices wo taxes": [["titre"]]})
    with pytest.raises(ValueError):
        marches.lire_bulletin(contenu, pays="FR")
