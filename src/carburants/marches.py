"""Cours du pétrole et du dollar, pour relier les prix à la pompe à leur principal coût.

- FRED (Réserve fédérale de Saint-Louis) : Brent daté en dollars par baril (DCOILBRENTEU) et dollars pour un euro
  (DEXUSEU), quotidiens, sans clé d'API.
- Bulletin pétrolier de la Commission européenne : prix moyens hebdomadaires à la pompe par pays, avec et sans taxes.
  Le fichier d'historique est publié sous un lien qui change : on le retrouve sur la page du bulletin.
"""

from __future__ import annotations

import io
import re
from urllib.parse import urljoin

import polars as pl
import requests

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={serie}"
SERIES_FRED = {"brent_usd_baril": "DCOILBRENTEU", "usd_par_eur": "DEXUSEU"}
PAGE_BULLETIN = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
ENTETES = {"User-Agent": "prix-carburants-bornes/0.1 (https://github.com/nitescuale/prix-carburants-bornes)"}
LITRES_PAR_BARIL = 158.987
TIMEOUT = 120


def lire_fred(contenu: bytes, nom: str) -> pl.DataFrame:
    """Une série FRED au format CSV : une date, une valeur (« . » ou vide quand le marché est fermé)."""
    brut = pl.read_csv(io.BytesIO(contenu), infer_schema=False)
    date, valeur = brut.columns[:2]
    return brut.select(
        date=pl.col(date).str.to_date("%Y-%m-%d"),
        **{nom: pl.col(valeur).cast(pl.Float64, strict=False)},
    ).drop_nulls()


def telecharger_fred() -> pl.DataFrame:
    """Brent et taux de change par jour ouvré, et le Brent converti en euros par litre."""
    tables = []
    for nom, serie in SERIES_FRED.items():
        reponse = requests.get(FRED_CSV.format(serie=serie), headers=ENTETES, timeout=TIMEOUT)
        reponse.raise_for_status()
        tables.append(lire_fred(reponse.content, nom))
    return combiner(*tables)


def combiner(brent: pl.DataFrame, change: pl.DataFrame) -> pl.DataFrame:
    # Les jours fériés diffèrent entre Londres (Brent) et New York (change) : on garde les jours où les deux existent
    return (
        brent.join(change, on="date", how="inner")
        .with_columns(brent_eur_litre=pl.col("brent_usd_baril") / pl.col("usd_par_eur") / LITRES_PAR_BARIL)
        .sort("date")
    )


def lien_historique_bulletin(html: str, base: str = PAGE_BULLETIN) -> str:
    """Le lien du fichier Excel d'historique des prix sur la page du bulletin pétrolier."""
    liens = re.findall(r'href="([^"]+)"', html)
    candidats = [lien for lien in liens if re.search(r"history", lien, re.IGNORECASE) and re.search(r"\.xlsx?", lien, re.IGNORECASE)]
    if not candidats:
        raise ValueError("Lien vers l'historique du bulletin pétrolier introuvable")
    # Plusieurs fichiers d'historique : celui des prix (avec ou sans taxes) plutôt que celui des taxes seules
    candidats.sort(key=lambda lien: "price" not in lien.lower())
    return urljoin(base, candidats[0].replace("&amp;", "&"))


def telecharger_bulletin() -> tuple[str, bytes]:
    """L'URL et le contenu brut du fichier d'historique du bulletin pétrolier européen."""
    page = requests.get(PAGE_BULLETIN, headers=ENTETES, timeout=TIMEOUT)
    page.raise_for_status()
    url = lien_historique_bulletin(page.text)
    fichier = requests.get(url, headers=ENTETES, timeout=TIMEOUT)
    fichier.raise_for_status()
    return url, fichier.content


FEUILLES_BULLETIN = {"ht": "Prices wo taxes", "ttc": "Prices with taxes"}
PRODUITS_BULLETIN = {"euro95": "sp95", "diesel": "gazole"}


def lire_bulletin(contenu: bytes, pays: str = "FR") -> pl.DataFrame:
    """Prix moyens hebdomadaires d'un pays dans l'historique du bulletin pétrolier, en euros par litre.

    Chaque feuille a une ligne de codes (« FR_price_wo_tax_diesel »), une ligne de libellés et une ligne d'unités
    (euros pour 1000 litres), puis une ligne par lundi, de la plus récente à la plus ancienne.
    """
    import openpyxl

    classeur = openpyxl.load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    colonnes: dict[str, list] = {}
    dates = None
    for suffixe, feuille in FEUILLES_BULLETIN.items():
        lignes = list(classeur[feuille].iter_rows(values_only=True))
        codes = re.compile(rf"^{pays}_price_w(?:o|ith)_tax_({'|'.join(PRODUITS_BULLETIN)})$")
        entetes = next((ligne for ligne in lignes[:10] if any(codes.match(str(c or "")) for c in ligne)), None)
        if entetes is None:
            raise ValueError(f"Colonnes du pays {pays} introuvables dans la feuille « {feuille} »")
        indices = {PRODUITS_BULLETIN[m.group(1)]: i for i, c in enumerate(entetes) if (m := codes.match(str(c or "")))}
        donnees = [ligne for ligne in lignes if hasattr(ligne[0], "year")]
        jours = [ligne[0].date() if hasattr(ligne[0], "date") else ligne[0] for ligne in donnees]
        if dates is None:
            dates = jours
        elif jours != dates:
            raise ValueError("Les feuilles avec et sans taxes n'ont pas les mêmes semaines")
        for produit, i in indices.items():
            colonnes[f"{produit}_{suffixe}"] = [_euros_par_litre(ligne[i]) for ligne in donnees]
    classeur.close()
    ordre = [f"{p}_{s}" for p in PRODUITS_BULLETIN.values() for s in FEUILLES_BULLETIN if f"{p}_{s}" in colonnes]
    return (
        pl.DataFrame({"date": dates, **{c: colonnes[c] for c in ordre}}, schema_overrides={c: pl.Float64 for c in ordre})
        .with_columns(pl.col("date").cast(pl.Date))
        .unique("date", keep="first")
        .sort("date")
    )


def _euros_par_litre(valeur) -> float | None:
    # Les cellules vides ou textuelles (« n.a. ») sont des semaines sans relevé
    if isinstance(valeur, (int, float)) and valeur > 0:
        return valeur / 1000
    return None
