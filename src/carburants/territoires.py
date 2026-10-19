"""Données territoriales du volet électrique : communes, voitures par type de recharge, prix de l'électricité.

- Communes (API Découpage administratif, geo.api.gouv.fr) : population, surface et centre de chaque commune, et des
  arrondissements de Paris, Lyon et Marseille, auxquels les bornes sont parfois rattachées.
- Voitures particulières immatriculées par commune et par type de recharge (Agence ORE, sur data.gouv.fr) : les
  fichiers sont conservés tels quels, avec leur provenance, et lus à l'analyse.
- Prix de l'électricité des ménages (Eurostat, nrg_pc_204), semestriel, toutes taxes comprises : il sert de prix de
  la recharge à domicile.
"""

from __future__ import annotations

import json
import re
from datetime import date

import polars as pl
import requests

ENTETES = {"User-Agent": "prix-carburants-bornes/0.1 (https://github.com/nitescuale/prix-carburants-bornes)"}
TIMEOUT = 300
TAILLE_MAX = 300_000_000  # au-delà, un fichier n'a pas sa place dans la branche data

URL_COMMUNES = "https://geo.api.gouv.fr/communes"
CHAMPS_COMMUNES = "code,nom,population,surface,centre,codeDepartement,codeRegion"
TYPES_COMMUNES = ["commune-actuelle", "arrondissement-municipal"]

DATASETS_API = "https://www.data.gouv.fr/api/1/datasets/"
SLUG_VEHICULES = "voitures-particulieres-immatriculees-par-commune-et-par-type-de-recharge"
RECHERCHE_VEHICULES = "voitures particulières immatriculées par commune et par type de recharge"

URL_EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_204"
# Ménages consommant de 2 500 à 5 000 kWh par an (la tranche de référence d'Eurostat), toutes taxes comprises
PARAMETRES_EUROSTAT = {
    "geo": "FR", "nrg_cons": "KWH2500-4999", "unit": "KWH", "tax": "I_TAX", "currency": "EUR", "format": "JSON", "lang": "fr",
}


def _get(url: str, **kwargs) -> requests.Response:
    reponse = requests.get(url, headers=ENTETES, timeout=TIMEOUT, **kwargs)
    reponse.raise_for_status()
    return reponse


def communes_vers_table(elements: list[dict], type_commune: str) -> pl.DataFrame:
    """Une ligne par commune : code INSEE, nom, population, surface (km²) et centre."""
    lignes = []
    for e in elements:
        centre = (e.get("centre") or {}).get("coordinates") or [None, None]
        surface = e.get("surface")
        lignes.append(
            {
                "code_insee": e["code"],
                "nom": e.get("nom"),
                "type": type_commune,
                "population": e.get("population"),
                "surface_km2": surface / 100 if surface is not None else None,  # l'API donne des hectares
                "longitude": centre[0],
                "latitude": centre[1],
                "departement": e.get("codeDepartement"),
                "region": e.get("codeRegion"),
            }
        )
    schema = {
        "code_insee": pl.Utf8, "nom": pl.Utf8, "type": pl.Utf8, "population": pl.Int64, "surface_km2": pl.Float64,
        "longitude": pl.Float64, "latitude": pl.Float64, "departement": pl.Utf8, "region": pl.Utf8,
    }
    return pl.DataFrame(lignes, schema=schema)


def telecharger_communes() -> pl.DataFrame:
    """Les communes et les arrondissements municipaux de France, avec leur population légale la plus récente."""
    tables = []
    for type_commune in TYPES_COMMUNES:
        reponse = _get(URL_COMMUNES, params={"fields": CHAMPS_COMMUNES, "type": type_commune, "format": "json"})
        tables.append(communes_vers_table(reponse.json(), type_commune))
    communes = pl.concat(tables)
    if communes.filter(pl.col("type") == "commune-actuelle").height < 30_000:
        raise ValueError(f"Seulement {communes.height} communes reçues : réponse incomplète ?")
    return communes


def choisir_ressources(jeu: dict) -> list[dict]:
    """Les fichiers de données d'un jeu data.gouv.fr (CSV, Excel ou Parquet), sans la documentation."""
    formats = {"csv", "xlsx", "xls", "parquet", "zip"}
    return [
        r
        for r in jeu.get("resources", [])
        if r.get("type") == "main"
        and (r.get("format") or "").lower() in formats
        and r.get("url")
        and (r.get("filesize") or 0) <= TAILLE_MAX
    ]


def jeu_vehicules() -> dict:
    """Les métadonnées du jeu des voitures par commune et par type de recharge, par son nom ou par recherche."""
    try:
        return _get(DATASETS_API + SLUG_VEHICULES + "/").json()
    except requests.HTTPError:
        resultats = _get(DATASETS_API, params={"q": RECHERCHE_VEHICULES, "page_size": 10}).json()["data"]
        for jeu in resultats:
            titre = (jeu.get("title") or "").lower()
            if "commune" in titre and "recharge" in titre:
                return jeu
        raise ValueError("Jeu des voitures par commune et par type de recharge introuvable sur data.gouv.fr")


def nom_fichier(ressource: dict) -> str:
    """Un nom de fichier sûr, tiré du titre de la ressource et de son format."""
    titre = re.sub(r"[^A-Za-z0-9_.-]+", "_", ressource.get("title") or ressource["id"]).strip("_")[:80]
    extension = (ressource.get("format") or "bin").lower()
    return titre if titre.lower().endswith("." + extension) else f"{titre}.{extension}"


def telecharger_vehicules() -> tuple[dict, list[tuple[str, bytes]]]:
    """Le manifeste (source, date, ressources) et le contenu brut de chaque fichier du jeu."""
    jeu = jeu_vehicules()
    ressources = choisir_ressources(jeu)
    if not ressources:
        raise ValueError(f"Aucun fichier de données dans le jeu {jeu.get('slug')}")
    fichiers, decrites = [], []
    for ressource in ressources:
        nom = nom_fichier(ressource)
        fichiers.append((nom, _get(ressource["url"]).content))
        decrites.append(
            {"fichier": nom, "titre": ressource.get("title"), "url": ressource["url"], "modifie": ressource.get("last_modified")}
        )
    manifeste = {
        "jeu": jeu.get("title"),
        "page": jeu.get("page"),
        "producteur": (jeu.get("organization") or {}).get("name"),
        "collecte": date.today().isoformat(),
        "ressources": decrites,
    }
    return manifeste, fichiers


def eurostat_vers_table(donnees: dict) -> pl.DataFrame:
    """Lit une réponse JSON-stat d'Eurostat dont seule la dimension du temps varie : semestre et prix (€/kWh)."""
    for nom in donnees["id"]:
        if nom != "time" and len(donnees["dimension"][nom]["category"]["index"]) != 1:
            raise ValueError(f"La dimension {nom} a plusieurs valeurs : préciser la requête")
    index = donnees["dimension"]["time"]["category"]["index"]
    valeurs = donnees.get("value", {})
    lignes = []
    for periode, position in sorted(index.items(), key=lambda item: item[1]):
        valeur = valeurs.get(str(position)) if isinstance(valeurs, dict) else valeurs[position]
        annee, semestre = re.fullmatch(r"(\d{4})-S([12])", periode).groups()
        lignes.append({"semestre": periode, "debut": date(int(annee), 1 if semestre == "1" else 7, 1), "prix_eur_kwh": valeur})
    return pl.DataFrame(lignes, schema={"semestre": pl.Utf8, "debut": pl.Date, "prix_eur_kwh": pl.Float64}).drop_nulls()


def telecharger_prix_electricite() -> pl.DataFrame:
    return eurostat_vers_table(_get(URL_EUROSTAT, params=PARAMETRES_EUROSTAT).json())


def ecrire_manifeste(chemin, manifeste: dict) -> None:
    chemin.write_text(json.dumps(manifeste, ensure_ascii=False, indent=1) + "\n")


# Départements de métropole (code → nom), pour nommer les résultats
DEPARTEMENTS = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence", "05": "Hautes-Alpes",
    "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes", "09": "Ariège", "10": "Aube", "11": "Aude",
    "12": "Aveyron", "13": "Bouches-du-Rhône", "14": "Calvados", "15": "Cantal", "16": "Charente",
    "17": "Charente-Maritime", "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs", "26": "Drôme",
    "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard", "31": "Haute-Garonne", "32": "Gers",
    "33": "Gironde", "34": "Hérault", "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire", "38": "Isère",
    "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire", "44": "Loire-Atlantique",
    "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne", "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche",
    "51": "Marne", "52": "Haute-Marne", "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan",
    "57": "Moselle", "58": "Nièvre", "59": "Nord", "60": "Oise", "61": "Orne", "62": "Pas-de-Calais",
    "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques", "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales",
    "67": "Bas-Rhin", "68": "Haut-Rhin", "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe",
    "73": "Savoie", "74": "Haute-Savoie", "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne",
    "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn", "82": "Tarn-et-Garonne", "83": "Var",
    "84": "Vaucluse", "85": "Vendée", "86": "Vienne", "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne",
    "90": "Territoire de Belfort", "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis",
    "94": "Val-de-Marne", "95": "Val-d'Oise",
}


def lire_vehicules(chemin) -> pl.DataFrame:
    """Le fichier des voitures par commune et par type de recharge (CSV de l'Agence ORE, encodé en latin-1).

    Colonnes : code_insee, date (fin de période), electriques (voitures rechargeables électriques, hybrides
    rechargeables comprises), gaz, voitures (toutes énergies). Les codes de Paris, Lyon et Marseille sont ceux des
    arrondissements.
    """
    brut = pl.read_csv(chemin, encoding="latin1", infer_schema=False)
    return brut.select(
        code_insee=pl.col("CODGEO").str.zfill(5),
        date=pl.col("DATE_ARRETE").str.to_date(),
        electriques=pl.col("NB_VP_RECHARGEABLES_EL").cast(pl.Int64),
        gaz=pl.col("NB_VP_RECHARGEABLES_GAZ").cast(pl.Int64),
        voitures=pl.col("NB_VP").cast(pl.Int64),
    )
