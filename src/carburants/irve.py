"""Fichier consolidé des bornes de recharge pour véhicules électriques (IRVE), publié sur data.gouv.fr."""

from __future__ import annotations

import io

import polars as pl
import requests

DATASET_API = "https://www.data.gouv.fr/api/1/datasets/fichier-consolide-des-bornes-de-recharge-pour-vehicules-electriques/"
TIMEOUT = 300


def url_fichier_consolide() -> str:
    """Trouve l'URL du CSV consolidé le plus récent via l'API data.gouv.fr."""
    reponse = requests.get(DATASET_API, timeout=TIMEOUT)
    reponse.raise_for_status()
    ressources = [
        r
        for r in reponse.json()["resources"]
        if (r.get("format") or "").lower() == "csv" and "consolid" in (r.get("title") or "").lower()
    ]
    if not ressources:
        raise ValueError("Aucun CSV consolidé trouvé dans le jeu de données IRVE")
    ressources.sort(key=lambda r: r.get("last_modified") or "", reverse=True)
    return ressources[0]["url"]


def lire_csv(contenu: bytes) -> pl.DataFrame:
    """Lit le CSV au schéma national IRVE ; toutes les colonnes restent en texte, le typage se fait à l'analyse."""
    return pl.read_csv(io.BytesIO(contenu), infer_schema=False, truncate_ragged_lines=True)


def telecharger() -> pl.DataFrame:
    reponse = requests.get(url_fichier_consolide(), timeout=TIMEOUT)
    reponse.raise_for_status()
    return lire_csv(reponse.content)
