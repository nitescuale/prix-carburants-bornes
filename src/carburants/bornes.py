"""Les bornes de recharge (fichier consolidé IRVE) prêtes à l'analyse.

Le fichier consolidé agrège les déclarations de centaines d'aménageurs : un même point de charge peut y figurer deux
fois, la puissance est parfois en watts, les booléens s'écrivent de dix façons et le tarif est un texte libre. Ce module
ramène tout cela à une ligne par point de charge, avec des colonnes typées.
"""

from __future__ import annotations

import re
from datetime import date

import numpy as np
import polars as pl
from sklearn.neighbors import BallTree

RAYON_TERRE_KM = 6371.0

# Classes de puissance, de la prise renforcée à la recharge ultra-rapide : (borne supérieure en kW, incluse ou non).
# 22 kW inclut les 22,08 kW (3 × 32 A) souvent déclarés ; 50 et 150 kW ouvrent la classe supérieure.
CLASSES_PUISSANCE = {
    "jusqu'à 7,4 kW": (7.4, True),
    "7,4 à 22 kW": (22.5, True),
    "22 à 50 kW": (50.0, False),
    "50 à 150 kW": (150.0, False),
    "150 kW et plus": (float("inf"), True),
}
RAPIDE_KW = 50.0  # seuil de la recharge rapide, celle qui sert sur un trajet

# Plateformes qui republient les points d'autres opérateurs : en cas de doublon, la déclaration de l'opérateur prime
AGREGATEURS = {"eco-movement", "gireve", "gireve-2", "alize"}
# Valeur d'attente du schéma, à ne pas prendre pour un identifiant
NON_CONCERNE = r"(?i)^\s*non\s+concern"

VRAI = {"true", "1", "oui", "vrai", "yes"}
FAUX = {"false", "0", "non", "faux", "no"}

# Un prix par kWh dans le texte libre du tarif : « 0,55€/kWh », « 0.3333€ par kwh », « 0,45 € TTC le kWh »…
_PRIX_TEXTE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:€|â‚¬|eur(?:os?)?|e)?\s*(?:ttc\s*)?(?:/|par|le|du)\s*kwh", re.IGNORECASE
)
# … ou en centimes : « 59 cts/kWh », « 49cts/Kwh »
_PRIX_CENTIMES = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:cts?|centimes?)\s*(?:ttc\s*)?(?:/|par|le|du)?\s*kwh", re.IGNORECASE)
# … ou le champ energyPrice des tarifs publiés en JSON
_PRIX_JSON = re.compile(r'"energyPrice"\s*:\s*(\d+(?:\.\d+)?)')
PRIX_PLAUSIBLE = (0.05, 1.5)  # €/kWh


def booleen(colonne: pl.Expr) -> pl.Expr:
    """« true », « True », « 1 », « oui »… en booléen ; null si la valeur n'est pas reconnue."""
    texte = colonne.cast(pl.Utf8).str.strip_chars().str.to_lowercase()
    return pl.when(texte.is_in(list(VRAI))).then(True).when(texte.is_in(list(FAUX))).then(False).otherwise(None)


def puissance_kw(colonne: pl.Expr) -> pl.Expr:
    """La puissance nominale en kW. Une valeur d'au moins 1 000 est lue en watts si elle donne une puissance
    plausible (22 000 → 22 kW), sinon écartée ; une puissance nulle est inconnue."""
    p = colonne.cast(pl.Utf8).str.replace(",", ".").cast(pl.Float64, strict=False)
    en_watts = p / 1000
    return (
        pl.when(p <= 0).then(None)
        .when(p >= 1000).then(pl.when(en_watts.is_between(1, 400)).then(en_watts).otherwise(None))
        .otherwise(p)
    )


def classe_puissance(puissance: pl.Expr) -> pl.Expr:
    """La classe de puissance d'un point de charge (null si la puissance est inconnue)."""
    expression = pl.when(puissance.is_null()).then(None)
    for nom, (borne, incluse) in CLASSES_PUISSANCE.items():
        expression = expression.when(puissance <= borne if incluse else puissance < borne).then(pl.lit(nom))
    return expression.otherwise(None)


def prix_kwh(tarif: str | None) -> float | None:
    """Le premier prix par kWh lisible dans le tarif, s'il est plausible ; None sinon."""
    if not tarif:
        return None
    trouve = _PRIX_JSON.search(tarif) or _PRIX_TEXTE.search(tarif)
    if trouve is not None:
        prix = float(trouve.group(1).replace(",", "."))
    elif (centimes := _PRIX_CENTIMES.search(tarif)) is not None:
        prix = float(centimes.group(1).replace(",", ".")) / 100
    else:
        return None
    return prix if PRIX_PLAUSIBLE[0] <= prix <= PRIX_PLAUSIBLE[1] else None


def _coordonnees(irve: pl.DataFrame) -> pl.DataFrame:
    """Longitude et latitude : les coordonnées consolidées, à défaut le champ « [lon,lat] » déclaré."""
    declarees = pl.col("coordonneesXY").str.strip_chars("[] ").str.split(",")
    return irve.with_columns(
        longitude=pl.coalesce(
            pl.col("consolidated_longitude").cast(pl.Float64, strict=False),
            declarees.list.get(0, null_on_oob=True).str.strip_chars().cast(pl.Float64, strict=False),
        ),
        latitude=pl.coalesce(
            pl.col("consolidated_latitude").cast(pl.Float64, strict=False),
            declarees.list.get(1, null_on_oob=True).str.strip_chars().cast(pl.Float64, strict=False),
        ),
    )


def en_metropole(longitude: pl.Expr, latitude: pl.Expr) -> pl.Expr:
    """Le point est-il en France métropolitaine (Corse comprise) ? Un cadre grossier, qui écarte surtout les erreurs."""
    return latitude.is_between(41.3, 51.2) & longitude.is_between(-5.3, 9.6)


def nettoyer(irve: pl.DataFrame, aujourd_hui: date | None = None) -> pl.DataFrame:
    """Une ligne par point de charge.

    Un même point est souvent publié deux fois, par son opérateur (ou la collectivité) et par une plateforme
    d'agrégation : on garde la déclaration de l'opérateur, puis celle dont la puissance est connue, puis la plus
    récente. L'identifiant « Non concerné » est ignoré ; un point sans identifiant garde sa propre ligne.
    """
    aujourd_hui = aujourd_hui or date.today()

    def identifiant(colonne: str) -> pl.Expr:
        valeur = pl.col(colonne).cast(pl.Utf8)
        return pl.when(valeur.str.contains(NON_CONCERNE)).then(None).otherwise(valeur)

    table = _coordonnees(irve).with_row_index("ligne").with_columns(
        id_pdc=pl.coalesce(identifiant("id_pdc_itinerance"), identifiant("id_pdc_local"),
                           pl.format("ligne-{}", pl.col("ligne"))),
        id_station_=pl.coalesce(identifiant("id_station_itinerance"), identifiant("id_station_local")),
        date_maj_=pl.col("date_maj").str.to_date(strict=False),
        operateur_direct=~pl.col("datagouv_organization_or_owner").is_in(list(AGREGATEURS)).fill_null(False),
        puissance_kw=puissance_kw(pl.col("puissance_nominale")),
    )
    table = (
        table.sort(
            "operateur_direct", pl.col("puissance_kw").is_not_null(), "date_maj_", "last_modified", "ligne",
            nulls_last=False,
        )
        .unique("id_pdc", keep="last", maintain_order=True)
    )
    mise_en_service = pl.col("date_mise_en_service").str.slice(0, 10).str.to_date(strict=False)
    return table.select(
        "id_pdc",
        id_station=pl.coalesce("id_station_", "id_pdc"),
        operateur=pl.col("nom_operateur"),
        amenageur=pl.col("nom_amenageur"),
        enseigne=pl.col("nom_enseigne"),
        implantation=pl.col("implantation_station").str.replace_all("[ŕā]", "à"),
        # « Accès réservé » (clients, résidents, flottes) ; l'encodage du mot « Accès » varie d'un aménageur à l'autre
        acces_reserve=pl.col("condition_acces").str.to_lowercase().str.contains("réserv|reserv"),
        code_insee=pl.col("code_insee_commune"),
        longitude=pl.col("longitude"),
        latitude=pl.col("latitude"),
        metropole=en_metropole(pl.col("longitude"), pl.col("latitude")).fill_null(False),
        puissance_kw=pl.col("puissance_kw"),
        classe=classe_puissance(pl.col("puissance_kw")),
        mise_en_service=pl.when(mise_en_service <= aujourd_hui).then(mise_en_service),
        gratuit=booleen(pl.col("gratuit")),
        prix_kwh=pl.col("tarification").map_elements(prix_kwh, return_dtype=pl.Float64),
        date_maj=pl.col("date_maj_"),
    )


def stations_recharge(points: pl.DataFrame) -> pl.DataFrame:
    """Une ligne par station de recharge : position, nombre de points de charge et puissance maximale."""
    return (
        points.group_by("id_station")
        .agg(
            pl.col("longitude", "latitude", "operateur", "implantation", "metropole").first(),
            points_de_charge=pl.len(),
            puissance_max_kw=pl.col("puissance_kw").max(),
            mise_en_service=pl.col("mise_en_service").min(),
        )
        .with_columns(classe=classe_puissance(pl.col("puissance_max_kw")))
        .sort("id_station")
    )


def _radians(table: pl.DataFrame) -> np.ndarray:
    return np.radians(table.select("latitude", "longitude").to_numpy())


def distance_plus_proche_km(origines: pl.DataFrame, cibles: pl.DataFrame) -> np.ndarray:
    """Distance à vol d'oiseau (km) de chaque origine à la cible la plus proche (colonnes latitude et longitude)."""
    if cibles.is_empty():
        return np.full(origines.height, np.nan)
    distances, _ = BallTree(_radians(cibles), metric="haversine").query(_radians(origines), k=1)
    return distances[:, 0] * RAYON_TERRE_KM


def part_population_au_dela(distances: np.ndarray, population: np.ndarray, seuils_km: list[float]) -> dict[float, float]:
    """Pour chaque seuil, la part de la population (en %) qui habite à plus de ce seuil de la cible la plus proche."""
    distances, population = np.asarray(distances, dtype=float), np.asarray(population, dtype=float)
    connue = ~np.isnan(distances) & ~np.isnan(population)
    total = population[connue].sum()
    return {s: float(population[connue & (distances > s)].sum() / total * 100) for s in seuils_km}


def sites(table: pl.DataFrame, rayon_km: float = 0.15) -> np.ndarray:
    """Un numéro de site par ligne : les stations à moins de `rayon_km` l'une de l'autre (de proche en proche)
    forment un même site, comme deux opérateurs sur un même parking."""
    from scipy.sparse.csgraph import connected_components
    from sklearn.neighbors import radius_neighbors_graph

    if table.is_empty():
        return np.zeros(0, dtype=int)
    graphe = radius_neighbors_graph(_radians(table), rayon_km / RAYON_TERRE_KM, metric="haversine")
    return connected_components(graphe, directed=False)[1]
