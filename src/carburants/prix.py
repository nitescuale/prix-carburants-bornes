"""Prix des carburants publiés par le ministère de l'Économie (prix-carburants.gouv.fr).

Deux flux au même format XML (encodé ISO-8859-1, zippé) :
- instantané : dernier prix connu de chaque station, mis à jour toutes les 10 minutes ;
- annuel : chaque changement de prix de chaque station sur une année, depuis 2007.
"""

from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import polars as pl
import requests

BASE_URL = "https://donnees.roulez-eco.fr/opendata"
URL_INSTANTANE = f"{BASE_URL}/instantane"
TIMEOUT = 120

STATIONS_SCHEMA = {
    "id": pl.Int64,
    "latitude": pl.Float64,
    "longitude": pl.Float64,
    "cp": pl.Utf8,
    "type_route": pl.Utf8,
    "adresse": pl.Utf8,
    "ville": pl.Utf8,
    "automate_24_24": pl.Boolean,
    "services": pl.List(pl.Utf8),
}

PRIX_SCHEMA = {
    "station_id": pl.Int64,
    "carburant": pl.Utf8,
    "carburant_id": pl.Int64,
    "maj": pl.Datetime("us"),
    "prix": pl.Float64,
}


def url_annee(annee: int) -> str:
    return f"{BASE_URL}/annee/{annee}"


def telecharger_xml(url: str) -> bytes:
    """Télécharge une archive zip et renvoie le XML qu'elle contient."""
    reponse = requests.get(url, timeout=TIMEOUT)
    reponse.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(reponse.content)) as archive:
        noms = [n for n in archive.namelist() if n.lower().endswith(".xml")]
        if len(noms) != 1:
            raise ValueError(f"Archive inattendue ({url}) : {archive.namelist()}")
        return archive.read(noms[0])


def _coordonnee(valeur: str | None) -> float | None:
    # Les coordonnées sont publiées en degrés multipliés par 100 000.
    if not valeur:
        return None
    try:
        return float(valeur) / 100_000
    except ValueError:
        return None


def _prix(valeur: str | None) -> float | None:
    # Les fichiers récents donnent des euros (1.759), les anciens des millièmes (1759).
    if not valeur:
        return None
    prix = float(valeur)
    return prix / 1000 if prix > 100 else prix


def _date(valeur: str | None) -> datetime | None:
    if not valeur:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(valeur, fmt)
        except ValueError:
            continue
    return None


def _entier(valeur: str | None) -> int | None:
    try:
        return int(valeur) if valeur else None
    except ValueError:
        return None


def parser_xml(contenu: bytes) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Transforme le XML en deux tables : stations et prix (une ligne par prix publié)."""
    stations: list[dict] = []
    prix: list[dict] = []

    for _, pdv in ET.iterparse(io.BytesIO(contenu), events=("end",)):
        if pdv.tag != "pdv":
            continue
        station_id = _entier(pdv.get("id"))
        horaires = pdv.find("horaires")
        stations.append(
            {
                "id": station_id,
                "latitude": _coordonnee(pdv.get("latitude")),
                "longitude": _coordonnee(pdv.get("longitude")),
                "cp": pdv.get("cp"),
                "type_route": {"A": "autoroute", "R": "route"}.get(pdv.get("pop", ""), pdv.get("pop")),
                "adresse": (pdv.findtext("adresse") or "").strip() or None,
                "ville": (pdv.findtext("ville") or "").strip() or None,
                "automate_24_24": (horaires is not None and horaires.get("automate-24-24") == "1"),
                "services": [s.text.strip() for s in pdv.iter("service") if s.text],
            }
        )
        for p in pdv.iter("prix"):
            if p.get("valeur") is None:
                continue
            prix.append(
                {
                    "station_id": station_id,
                    "carburant": p.get("nom"),
                    "carburant_id": _entier(p.get("id")),
                    "maj": _date(p.get("maj")),
                    "prix": _prix(p.get("valeur")),
                }
            )
        pdv.clear()

    return (
        pl.DataFrame(stations, schema=STATIONS_SCHEMA),
        pl.DataFrame(prix, schema=PRIX_SCHEMA).unique().sort(["station_id", "carburant", "maj"]),
    )
