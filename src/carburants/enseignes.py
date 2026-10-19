"""Enseigne des stations-service, absente du flux des prix, retrouvée dans OpenStreetMap.

Chaque station du flux est rapprochée de la station OpenStreetMap (amenity=fuel) la plus proche,
si elle est à moins de RAYON_M mètres.
"""

from __future__ import annotations

import re
import time

import numpy as np
import polars as pl
import requests
from sklearn.neighbors import BallTree

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
REQUETE = """
[out:json][timeout:600];
area["ISO3166-1"="FR"][admin_level=2]->.fr;
nwr["amenity"="fuel"](area.fr);
out center tags;
"""
RAYON_M = 250
RAYON_TERRE_M = 6_371_000

# Motif (sur la marque ou le nom en minuscules) -> enseigne normalisée
ENSEIGNES = [
    (r"total|elan\b", "TotalEnergies"),
    (r"leclerc", "E.Leclerc"),
    (r"intermarch", "Intermarché"),
    (r"carrefour", "Carrefour"),
    (r"super u\b|hyper u\b|u express|syst[eè]me u|^u$", "Système U"),
    (r"auchan", "Auchan"),
    (r"casino|g[ée]ant", "Casino"),
    (r"\besso\b", "Esso"),
    (r"^bp\b|\bbp\b", "BP"),
    (r"shell", "Shell"),
    (r"\bavia\b", "Avia"),
    (r"dyneff", "Dyneff"),
    (r"\bagip\b|\beni\b", "Eni"),
    (r"netto", "Netto"),
    (r"\bcora\b", "Cora"),
]
GRANDES_SURFACES = {"E.Leclerc", "Intermarché", "Carrefour", "Système U", "Auchan", "Casino", "Netto", "Cora"}


def normaliser(marque: str | None, nom: str | None = None) -> str | None:
    """Ramène les variantes (« Total Access », « Super U »...) à une enseigne.

    La marque OSM est prioritaire ; le nom ne sert que s'il contient une enseigne connue.
    """
    for texte, garder_brut in ((marque, True), (nom, False)):
        if not texte:
            continue
        bas = texte.strip().lower()
        for motif, enseigne in ENSEIGNES:
            if re.search(motif, bas):
                return enseigne
        if garder_brut:
            return texte.strip()
    return None


def telecharger_osm() -> pl.DataFrame:
    """Toutes les stations-service OpenStreetMap de France, avec leurs coordonnées et leurs tags."""
    derniere_erreur: Exception | None = None
    for tentative, url in enumerate(OVERPASS * 2):
        try:
            reponse = requests.post(url, data={"data": REQUETE}, timeout=900)
            reponse.raise_for_status()
            return elements_vers_table(reponse.json()["elements"])
        except (requests.RequestException, ValueError) as erreur:
            derniere_erreur = erreur
            time.sleep(30 * (tentative + 1))
    raise RuntimeError(f"Overpass indisponible : {derniere_erreur}")


def elements_vers_table(elements: list[dict]) -> pl.DataFrame:
    lignes = []
    for e in elements:
        point = e if "lat" in e else e.get("center")
        if not point:
            continue
        tags = e.get("tags", {})
        lignes.append(
            {
                "osm_id": f"{e['type']}/{e['id']}",
                "latitude": point["lat"],
                "longitude": point["lon"],
                "osm_marque": tags.get("brand"),
                "osm_nom": tags.get("name"),
            }
        )
    schema = {"osm_id": pl.Utf8, "latitude": pl.Float64, "longitude": pl.Float64, "osm_marque": pl.Utf8, "osm_nom": pl.Utf8}
    return pl.DataFrame(lignes, schema=schema).with_columns(
        enseigne=pl.struct("osm_marque", "osm_nom").map_elements(
            lambda r: normaliser(r["osm_marque"], r["osm_nom"]), return_dtype=pl.Utf8
        )
    )


def apparier(stations: pl.DataFrame, osm: pl.DataFrame, rayon_m: float = RAYON_M) -> pl.DataFrame:
    """Associe à chaque station du flux la station OSM la plus proche (ou rien au-delà du rayon)."""
    stations = stations.drop_nulls(["latitude", "longitude"])
    arbre = BallTree(np.radians(osm.select("latitude", "longitude").to_numpy()), metric="haversine")
    distances, indices = arbre.query(np.radians(stations.select("latitude", "longitude").to_numpy()), k=1)
    distances_m = distances[:, 0] * RAYON_TERRE_M
    proches = osm[indices[:, 0]].with_columns(distance_m=pl.Series(distances_m).round(1))
    resultat = pl.concat([stations.select(station_id="id"), proches.select("osm_id", "osm_marque", "osm_nom", "enseigne", "distance_m")], how="horizontal")
    hors_rayon = pl.col("distance_m") > rayon_m
    return resultat.with_columns(
        [pl.when(hors_rayon).then(None).otherwise(pl.col(c)).alias(c) for c in ("osm_id", "osm_marque", "osm_nom", "enseigne")]
    ).with_columns(grande_surface=pl.col("enseigne").is_in(list(GRANDES_SURFACES)).fill_null(False))
