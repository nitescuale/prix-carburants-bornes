"""Enseigne des stations-service, absente du flux des prix, retrouvée dans OpenStreetMap.

Chaque station du flux est rapprochée de la station OpenStreetMap (amenity=fuel) la plus proche,
si elle est à moins de RAYON_M mètres.
"""

from __future__ import annotations

import re
import time
import unicodedata

import numpy as np
import polars as pl
import requests
from sklearn.neighbors import BallTree

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
ENTETES = {"User-Agent": "prix-carburants-bornes/0.1 (https://github.com/nitescuale/prix-carburants-bornes)"}
# Une requête sur toute la France est trop lourde pour les serveurs Overpass : on découpe
# la métropole et la Corse en tuiles (sud, ouest, nord, est).
TUILES = [
    (41.0, -5.5, 44.5, 2.5),
    (41.0, 2.5, 44.5, 10.0),
    (44.5, -5.5, 48.0, 2.5),
    (44.5, 2.5, 48.0, 10.0),
    (48.0, -5.5, 51.5, 2.5),
    (48.0, 2.5, 51.5, 10.0),
]
REQUETE = """
[out:json][timeout:300];
nwr["amenity"="fuel"]({sud},{ouest},{nord},{est});
out center tags;
"""
RAYON_M = 250
RAYON_TERRE_M = 6_371_000

# Motif (sur la marque ou le nom, en minuscules et sans accents) -> enseigne normalisée
ENSEIGNES = [
    (r"total|elan\b", "TotalEnergies"),
    (r"leclerc", "E.Leclerc"),
    (r"intermarche", "Intermarché"),
    (r"carrefour", "Carrefour"),
    (r"super u\b|hyper u\b|u express|systeme u|^u$", "Système U"),
    (r"auchan", "Auchan"),
    (r"casino", "Casino"),
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


# Préfixes génériques retirés du nom avant d'y chercher une enseigne
PREFIXE_NOM = re.compile(r"^(station(s)?([ -]service)?|relais)\s+")


def _simplifier(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return sans_accents.strip().lower()


def normaliser(marque: str | None, nom: str | None = None) -> str | None:
    """Ramène les variantes (« Total Access », « Super U »...) à une enseigne.

    La marque OSM est prioritaire. Le nom ne sert que s'il commence par une enseigne connue
    (« Station Avia du Lac » oui, « Garage du Carrefour » non).
    """
    if marque and marque.strip():
        bas = _simplifier(marque)
        for motif, enseigne in ENSEIGNES:
            if re.search(motif, bas):
                return enseigne
        return marque.strip()
    if nom:
        bas = PREFIXE_NOM.sub("", _simplifier(nom))
        for motif, enseigne in ENSEIGNES:
            if re.match(motif, bas):
                return enseigne
    return None


def _requete_overpass(requete: str) -> list[dict]:
    erreurs = []
    for tentative in range(6):
        url = OVERPASS[tentative % len(OVERPASS)]
        try:
            reponse = requests.post(url, data={"data": requete}, headers=ENTETES, timeout=400)
            reponse.raise_for_status()
            contenu = reponse.json()
            # En cas de dépassement de délai, Overpass répond 200 avec une remarque et une liste tronquée
            if "error" in contenu.get("remark", "").lower():
                raise ValueError(f"réponse partielle : {contenu['remark']}")
            if not contenu.get("elements"):
                raise ValueError("aucune station dans la réponse")
            return contenu["elements"]
        except (requests.RequestException, ValueError, KeyError) as erreur:
            erreurs.append(f"{url} : {erreur}")
            print(f"Overpass, tentative {tentative + 1} échouée : {erreur}")
            time.sleep(30 * (tentative + 1))
    raise RuntimeError("Overpass indisponible :\n" + "\n".join(erreurs))


def telecharger_osm() -> pl.DataFrame:
    """Toutes les stations-service OpenStreetMap de métropole et de Corse, avec leurs coordonnées et leurs tags."""
    elements: list[dict] = []
    for sud, ouest, nord, est in TUILES:
        elements += _requete_overpass(REQUETE.format(sud=sud, ouest=ouest, nord=nord, est=est))
        time.sleep(5)
    # Une station à cheval sur deux tuiles apparaît deux fois
    return elements_vers_table(elements).unique("osm_id", keep="first", maintain_order=True)


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
    """Associe à chaque station du flux la station OSM la plus proche (ou rien au-delà du rayon).

    Deux stations du flux très proches peuvent recevoir la même station OSM : osm_partage le signale.
    """
    if osm.is_empty():
        raise ValueError("Aucune station OpenStreetMap à apparier")
    stations = stations.drop_nulls(["latitude", "longitude"])
    arbre = BallTree(np.radians(osm.select("latitude", "longitude").to_numpy()), metric="haversine")
    distances, indices = arbre.query(np.radians(stations.select("latitude", "longitude").to_numpy()), k=1)
    distances_m = distances[:, 0] * RAYON_TERRE_M
    proches = osm[indices[:, 0]].with_columns(distance_m=pl.Series(distances_m).round(1))
    resultat = pl.concat([stations.select(station_id="id"), proches.select("osm_id", "osm_marque", "osm_nom", "enseigne", "distance_m")], how="horizontal")
    hors_rayon = pl.col("distance_m") > rayon_m
    return resultat.with_columns(
        [pl.when(hors_rayon).then(None).otherwise(pl.col(c)).alias(c) for c in ("osm_id", "osm_marque", "osm_nom", "enseigne")]
    ).with_columns(
        grande_surface=pl.col("enseigne").is_in(list(GRANDES_SURFACES)).fill_null(False),
        osm_partage=(pl.len().over("osm_id") > 1) & pl.col("osm_id").is_not_null(),
    )
