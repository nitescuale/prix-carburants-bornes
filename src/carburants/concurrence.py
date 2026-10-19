"""Concurrence locale entre stations : voisins, ouvertures de stations et étude d'événement.

Les distances sont calculées sur la sphère (formule de haversine) avec un arbre de recherche (BallTree), en kilomètres.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
from sklearn.neighbors import BallTree

RAYON_TERRE_KM = 6371.0


def _radians(table: pl.DataFrame) -> np.ndarray:
    return np.radians(table.select("latitude", "longitude").to_numpy())


def voisins(stations: pl.DataFrame, rayons_km: list[float]) -> pl.DataFrame:
    """Pour chaque station, le nombre d'autres stations à moins de chaque rayon et la distance de la plus proche."""
    if stations.height < 2:
        raise ValueError("Il faut au moins deux stations")
    coords = _radians(stations)
    arbre = BallTree(coords, metric="haversine")
    distances, _ = arbre.query(coords, k=2)
    colonnes = {f"voisins_{r:g}km": arbre.query_radius(coords, r / RAYON_TERRE_KM, count_only=True) - 1 for r in rayons_km}
    return stations.with_columns(
        **{nom: pl.Series(valeurs, dtype=pl.Int32) for nom, valeurs in colonnes.items()},
        plus_proche_km=pl.Series(distances[:, 1] * RAYON_TERRE_KM),
    )


def distance_plus_proche(stations: pl.DataFrame, cibles: pl.DataFrame, colonne_id: str = "station_id") -> pl.Series:
    """Distance de chaque station à la cible la plus proche autre qu'elle-même (par exemple une grande surface)."""
    if cibles.is_empty():
        return pl.Series("distance_km", [None] * stations.height, dtype=pl.Float64)
    arbre = BallTree(_radians(cibles), metric="haversine")
    k = min(2, cibles.height)
    distances, indices = arbre.query(_radians(stations), k=k)
    ids_cibles = cibles[colonne_id].to_numpy()
    soi_meme = ids_cibles[indices[:, 0]] == stations[colonne_id].to_numpy()
    if k == 1:
        resultat = np.where(soi_meme, np.nan, distances[:, 0])
    else:
        resultat = np.where(soi_meme, distances[:, 1], distances[:, 0])
    return pl.Series("distance_km", resultat * RAYON_TERRE_KM).fill_nan(None)


def ouvertures(
    vies: pl.DataFrame, depuis: date, rayon_remplacement_m: float = 300, jours_remplacement: int = 120
) -> pl.DataFrame:
    """Les stations apparues après `depuis` qui ne remplacent pas une station voisine.

    `vies` donne pour chaque station sa position et ses semaines de début et de fin de publication. Une station
    qui apparaît à moins de `rayon_remplacement_m` d'une station disparue à quelques mois près est un changement
    d'identifiant (reprise, nouvelle enseigne), pas une ouverture.
    """
    coords = _radians(vies)
    arbre = BallTree(coords, metric="haversine")
    candidates = np.flatnonzero((vies["debut"] >= depuis).to_numpy())
    if candidates.size == 0:
        return vies.clear()
    debuts, fins = vies["debut"].to_numpy(), vies["fin"].to_numpy()
    voisines = arbre.query_radius(coords[candidates], rayon_remplacement_m / 1000 / RAYON_TERRE_KM)
    ecart_max = np.timedelta64(jours_remplacement, "D")
    remplace = [
        any(j != i and abs(fins[j] - debuts[i]) <= ecart_max for j in proches) for i, proches in zip(candidates, voisines)
    ]
    return vies[candidates].filter(~pl.Series(remplace, dtype=pl.Boolean))


def paires_evenement(
    vies: pl.DataFrame,
    evenements: pl.DataFrame,
    rayon_traite_km: float = 3,
    temoins_km: tuple[float, float] = (10, 20),
    avant: int = 26,
    apres: int = 52,
) -> pl.DataFrame:
    """Les stations proches (traitées) et éloignées (témoins) de chaque ouverture, présentes sur toute la fenêtre."""
    arbre = BallTree(_radians(vies), metric="haversine")
    ids, debuts, fins = vies["station_id"].to_numpy(), vies["debut"].to_numpy(), vies["fin"].to_numpy()
    voisines, distances = arbre.query_radius(_radians(evenements), temoins_km[1] / RAYON_TERRE_KM, return_distance=True)
    lignes = []
    for evenement, proches, km in zip(evenements.iter_rows(named=True), voisines, distances * RAYON_TERRE_KM):
        t0 = np.datetime64(evenement["debut"])
        presentes = (debuts[proches] <= t0 - np.timedelta64(7 * avant, "D")) & (fins[proches] >= t0 + np.timedelta64(7 * apres, "D"))
        for j, d in zip(proches[presentes], km[presentes]):
            if ids[j] == evenement["station_id"]:
                continue
            groupe = "traitee" if d <= rayon_traite_km else "temoin" if d >= temoins_km[0] else None
            if groupe:
                lignes.append((evenement["station_id"], evenement["debut"], int(ids[j]), groupe, float(d)))
    return pl.DataFrame(
        lignes,
        schema={"evenement": pl.Int64, "t0": pl.Date, "station_id": pl.Int64, "groupe": pl.Utf8, "distance_km": pl.Float64},
        orient="row",
    )


def differences_evenement(
    panel: pl.DataFrame, paires: pl.DataFrame, avant: int = 26, apres: int = 52, reference: int = -4
) -> pl.DataFrame:
    """Écart de prix entre stations traitées et témoins, par ouverture et par semaine relative `k`.

    Pour chaque ouverture, on compare la moyenne des traitées et celle des témoins (différence de différences),
    recentrée sur sa moyenne avant la semaine `reference`.
    """
    moyennes = (
        paires.join(panel.select("station_id", "semaine", "ecart"), on="station_id")
        .with_columns(k=((pl.col("semaine") - pl.col("t0")).dt.total_days() // 7).cast(pl.Int32))
        .filter(pl.col("k").is_between(-avant, apres))
        .group_by("evenement", "k", "groupe")
        .agg(pl.col("ecart").mean())
    )
    groupes = set(moyennes["groupe"].unique())
    if not {"traitee", "temoin"} <= groupes:
        return pl.DataFrame(schema={"evenement": pl.Int64, "k": pl.Int32, "difference": pl.Float64})
    return (
        moyennes.pivot(on="groupe", index=["evenement", "k"], values="ecart")
        .drop_nulls(["traitee", "temoin"])
        .with_columns(difference=pl.col("traitee") - pl.col("temoin"))
        .with_columns(difference=pl.col("difference") - pl.col("difference").filter(pl.col("k") < reference).mean().over("evenement"))
        .drop_nulls("difference")
        .select("evenement", "k", "difference")
    )


def profil_evenement(differences: pl.DataFrame) -> pl.DataFrame:
    """Moyenne des différences par semaine relative, avec son erreur type (les ouvertures sont les unités)."""
    return (
        differences.group_by("k")
        .agg(effet=pl.col("difference").mean(), erreur=pl.col("difference").std() / pl.len().sqrt(), ouvertures=pl.len())
        .sort("k")
    )


def effet_moyen(
    differences: pl.DataFrame, debut: int, fin: int, grappes: pl.DataFrame | None = None
) -> dict[str, float]:
    """Effet moyen entre les semaines `debut` et `fin` après l'ouverture : moyenne par ouverture, puis sur les ouvertures.

    L'erreur type traite les ouvertures comme indépendantes ; avec `grappes` (colonnes `evenement` et `grappe`, par
    exemple le département), elle est regroupée par grappe, pour des ouvertures voisines qui partagent des témoins.
    """
    par_ouverture = differences.filter(pl.col("k").is_between(debut, fin)).group_by("evenement").agg(pl.col("difference").mean())
    valeurs = par_ouverture["difference"]
    n = valeurs.len()
    if n < 2:
        erreur = float("nan")
    elif grappes is None:
        erreur = valeurs.std() / np.sqrt(n)
    else:
        sommes = (
            par_ouverture.join(grappes, on="evenement", how="left")
            .group_by("grappe")
            .agg(somme=(pl.col("difference") - valeurs.mean()).sum())["somme"]
        )
        g = sommes.len()
        erreur = float(np.sqrt((sommes**2).sum() * g / (g - 1)) / n) if g > 1 else float("nan")
    return {"effet": valeurs.mean(), "erreur": erreur, "ouvertures": n}
