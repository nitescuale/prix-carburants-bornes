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
