"""Prix des carburants publiés par le ministère de l'Économie (prix-carburants.gouv.fr).

Deux flux au même format XML (encodé ISO-8859-1, zippé) :
- instantané : dernier prix connu de chaque station, mis à jour toutes les 10 minutes ;
- annuel : chaque changement de prix de chaque station sur une année, depuis 2007.
"""

from __future__ import annotations

import io
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import BinaryIO

import polars as pl
import requests

BASE_URL = "https://donnees.roulez-eco.fr/opendata"
URL_INSTANTANE = f"{BASE_URL}/instantane"
TIMEOUT = 120
# Au-delà, un prix est forcément en millièmes d'euro ; en dehors de PRIX_PLAUSIBLES, c'est une erreur de saisie
SEUIL_MILLIEMES = 10
PRIX_PLAUSIBLES = (0.2, 4.0)

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
    # Fermeture déclarée : T (temporaire) ou D (définitive), avec ses dates
    "fermeture_type": pl.Utf8,
    "fermeture_debut": pl.Utf8,
    "fermeture_fin": pl.Utf8,
    # Carburants en rupture de stock sans date de fin
    "ruptures": pl.List(pl.Utf8),
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


def _nom_xml(archive: zipfile.ZipFile, origine: str) -> str:
    noms = [n for n in archive.namelist() if n.lower().endswith(".xml")]
    if len(noms) != 1:
        raise ValueError(f"Archive inattendue ({origine}) : {archive.namelist()}")
    return noms[0]


def telecharger_xml(url: str) -> bytes:
    """Télécharge une archive zip et renvoie le XML qu'elle contient (pour les petits fichiers)."""
    reponse = requests.get(url, timeout=TIMEOUT)
    reponse.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(reponse.content)) as archive:
        return archive.read(_nom_xml(archive, url))


def collecter_zip(url: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Télécharge une archive sur disque puis lit son XML en flux.

    Les fichiers annuels pèsent plusieurs centaines de Mo une fois décompressés :
    on ne les charge jamais entièrement en mémoire.
    """
    with tempfile.TemporaryDirectory() as dossier:
        chemin = Path(dossier) / "archive.zip"
        with requests.get(url, timeout=TIMEOUT, stream=True) as reponse:
            reponse.raise_for_status()
            with chemin.open("wb") as fichier:
                for bloc in reponse.iter_content(chunk_size=1 << 20):
                    fichier.write(bloc)
        with zipfile.ZipFile(chemin) as archive, archive.open(_nom_xml(archive, url)) as xml:
            return parser_xml(xml)


def _coordonnee(valeur: str | None) -> float | None:
    # Les coordonnées sont publiées en degrés multipliés par 100 000, parfois déjà en degrés.
    if not valeur:
        return None
    try:
        nombre = float(valeur)
    except ValueError:
        return None
    degres = nombre / 100_000 if abs(nombre) > 180 else nombre
    # 0 signifie « non renseigné »
    return degres if degres != 0 and abs(degres) <= 180 else None


def _entier(valeur: str | None) -> int | None:
    try:
        return int(valeur) if valeur else None
    except ValueError:
        return None


def _lire_dates(texte: pl.Expr) -> pl.Expr:
    """Lit les dates de mise à jour, dont le format varie selon les années.

    Récent : « 2025-01-02 09:25:22 » ou « 2025-01-02T09:25:22 ». Les fichiers anciens ajoutent
    parfois des fractions de seconde ou un fuseau, ou omettent les secondes : on ne garde que
    les 19 premiers caractères et on essaie les formats du plus précis au moins précis.
    """
    normalise = texte.str.replace("T", " ").str.strip_chars()
    return pl.coalesce(
        normalise.str.slice(0, 19).str.to_datetime("%Y-%m-%d %H:%M:%S", time_unit="us", strict=False),
        normalise.str.slice(0, 16).str.to_datetime("%Y-%m-%d %H:%M", time_unit="us", strict=False),
        normalise.str.slice(0, 10).str.to_datetime("%Y-%m-%d", time_unit="us", strict=False),
    )


def parser_xml(source: bytes | BinaryIO) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Transforme le XML en deux tables : stations et prix (une ligne par prix publié).

    Les valeurs sont accumulées colonne par colonne, ce qui tient en mémoire même pour
    une année complète (plusieurs millions de prix).
    """
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    stations: dict[str, list] = {nom: [] for nom in STATIONS_SCHEMA}
    # Dates et prix restent en texte pendant la lecture, puis sont convertis d'un coup par polars
    prix: dict[str, list] = {"station_id": [], "carburant": [], "carburant_id": [], "maj": [], "valeur": []}
    noms: dict[str, str] = {}  # une seule copie de chaque nom de carburant

    for _, pdv in ET.iterparse(source, events=("end",)):
        if pdv.tag != "pdv":
            continue
        station_id = _entier(pdv.get("id"))
        horaires = pdv.find("horaires")
        stations["id"].append(station_id)
        stations["latitude"].append(_coordonnee(pdv.get("latitude")))
        stations["longitude"].append(_coordonnee(pdv.get("longitude")))
        stations["cp"].append(pdv.get("cp"))
        stations["type_route"].append({"A": "autoroute", "R": "route"}.get(pdv.get("pop", ""), pdv.get("pop")))
        stations["adresse"].append((pdv.findtext("adresse") or "").strip() or None)
        stations["ville"].append((pdv.findtext("ville") or "").strip() or None)
        stations["automate_24_24"].append(horaires is not None and horaires.get("automate-24-24") == "1")
        stations["services"].append([s.text.strip() for s in pdv.iter("service") if s.text])
        fermeture = pdv.find("fermeture")
        attributs = fermeture.attrib if fermeture is not None else {}
        for champ in ("type", "debut", "fin"):
            stations[f"fermeture_{champ}"].append(attributs.get(champ) or None)
        stations["ruptures"].append(sorted({r.get("nom") for r in pdv.iter("rupture") if r.get("nom") and not r.get("fin")}))
        for p in pdv.iter("prix"):
            if p.get("valeur") is None:
                continue
            nom = p.get("nom")
            prix["station_id"].append(station_id)
            prix["carburant"].append(noms.setdefault(nom, nom) if nom else None)
            prix["carburant_id"].append(_entier(p.get("id")))
            prix["maj"].append(p.get("maj"))
            prix["valeur"].append(p.get("valeur"))
        pdv.clear()

    valeur = pl.col("valeur").cast(pl.Float64, strict=False)
    bas, haut = PRIX_PLAUSIBLES
    tarifs = (
        pl.DataFrame(
            prix,
            schema={"station_id": pl.Int64, "carburant": pl.Utf8, "carburant_id": pl.Int64, "maj": pl.Utf8, "valeur": pl.Utf8},
        )
        .with_columns(
            maj=_lire_dates(pl.col("maj")),
            # Les fichiers récents donnent des euros (1.759), les anciens des millièmes (1759)
            prix=pl.when(valeur > SEUIL_MILLIEMES).then(valeur / 1000).otherwise(valeur),
        )
        .select(list(PRIX_SCHEMA))
        .cast(PRIX_SCHEMA)
    )
    illisibles = [brut for brut, lu in zip(prix["maj"], tarifs["maj"]) if brut and lu is None]
    if illisibles:
        print(f"{len(illisibles)} dates illisibles, par exemple : {illisibles[:5]}", file=sys.stderr)
    aberrants = tarifs.filter(~pl.col("prix").is_between(bas, haut))
    if aberrants.height:
        exemples = aberrants["prix"].head(5).to_list()
        print(f"{aberrants.height} prix hors de [{bas} ; {haut}] € écartés, par exemple : {exemples}", file=sys.stderr)
    tarifs = (
        tarifs.filter(pl.col("prix").is_between(bas, haut))
        # Plusieurs prix pour la même station, le même carburant et la même minute : le dernier publié l'emporte
        .unique(["station_id", "carburant", "maj"], keep="last", maintain_order=True)
        .sort(["station_id", "carburant", "maj"])
    )
    return pl.DataFrame(stations, schema=STATIONS_SCHEMA), tarifs
