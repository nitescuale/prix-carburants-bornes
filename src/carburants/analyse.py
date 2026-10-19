"""Mise en forme de l'historique des prix pour l'analyse : une série hebdomadaire par station et par carburant.

L'historique annuel liste chaque changement de prix. Une station qui ne change pas son prix pendant trois semaines
n'apparaît pas pendant trois semaines : on prolonge donc le dernier prix connu, mais pas indéfiniment (une station
qui ne publie plus rien est probablement fermée).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from carburants.prix import PRIX_PLAUSIBLES

SEMAINE = "1w"
VALIDITE_SEMAINES = 4


def charger_historique(dossier: Path | str, annees: list[int] | None = None) -> pl.LazyFrame:
    """Les fichiers prix_AAAA.parquet de la release « historique », lus à la demande."""
    dossier = Path(dossier)
    fichiers = sorted(dossier.glob("prix_*.parquet"))
    if annees is not None:
        fichiers = [f for f in fichiers if int(f.stem.removeprefix("prix_")) in annees]
    if not fichiers:
        raise FileNotFoundError(f"Aucun fichier prix_AAAA.parquet dans {dossier}")
    bas, haut = PRIX_PLAUSIBLES
    # Les fichiers publiés avant le filtrage à la lecture contiennent encore quelques prix aberrants
    return pl.scan_parquet(fichiers).filter(pl.col("prix").is_between(bas, haut) & pl.col("maj").is_not_null())


def prix_hebdo(prix: pl.LazyFrame | pl.DataFrame, validite_semaines: int = VALIDITE_SEMAINES) -> pl.DataFrame:
    """Un prix par station, carburant et semaine (lundi), le dernier publié dans la semaine.

    Une semaine sans changement reprend le prix précédent, au plus validite_semaines - 1 semaines après sa
    publication. Colonnes : station_id, carburant, semaine (date du lundi), prix, publie (le prix a été publié
    cette semaine-là, et non prolongé).
    """
    derniere = prix.lazy().select(pl.col("maj").max().dt.truncate(SEMAINE).dt.date()).collect().item()
    prolongation = pl.duration(weeks=validite_semaines - 1)
    return (
        prix.lazy()
        .with_columns(semaine=pl.col("maj").dt.truncate(SEMAINE).dt.date())
        .sort("maj")
        .group_by("station_id", "carburant", "semaine")
        .agg(pl.col("prix").last())
        .sort("station_id", "carburant", "semaine")
        .with_columns(suivante=pl.col("semaine").shift(-1).over("station_id", "carburant"))
        .with_columns(
            fin=pl.min_horizontal(
                pl.col("suivante") - pl.duration(weeks=1),
                pl.col("semaine") + prolongation,
                pl.lit(derniere),
            )
        )
        .with_columns(semaines=pl.date_ranges("semaine", "fin", interval=SEMAINE))
        .explode("semaines")
        .select(
            "station_id",
            "carburant",
            pl.col("semaines").alias("semaine"),
            "prix",
            publie=pl.col("semaines") == pl.col("semaine"),
        )
        .collect()
    )


def statistiques_hebdo(hebdo: pl.DataFrame, par: list[str] | None = None) -> pl.DataFrame:
    """Médiane, 10e et 90e centiles et nombre de stations, par semaine et carburant (et par les colonnes de par)."""
    cles = ["carburant", "semaine", *(par or [])]
    return (
        hebdo.group_by(cles)
        .agg(
            stations=pl.len(),
            mediane=pl.col("prix").median(),
            p10=pl.col("prix").quantile(0.10),
            p90=pl.col("prix").quantile(0.90),
        )
        .sort(cles)
    )


def stations_connues(dossier: Path | str) -> pl.DataFrame:
    """La description la plus récente de chaque station, d'après les fichiers stations_AAAA.parquet."""
    fichiers = sorted(Path(dossier).glob("stations_*.parquet"))
    if not fichiers:
        raise FileNotFoundError(f"Aucun fichier stations_AAAA.parquet dans {dossier}")
    tables = [
        pl.read_parquet(f, columns=["id", "cp", "type_route", "latitude", "longitude"]).with_columns(
            annee=pl.lit(int(f.stem.removeprefix("stations_")))
        )
        for f in fichiers
    ]
    return (
        pl.concat(tables)
        .sort("annee")
        .unique("id", keep="last")
        .with_columns(departement=departement(pl.col("cp")))
        .sort("id")
    )


def departement(cp: pl.Expr) -> pl.Expr:
    """Département à partir du code postal : 2 chiffres, 3 pour l'outre-mer (la Corse, codes postaux 20xxx, donne 20)."""
    cp = cp.str.strip_chars().str.zfill(5)
    return pl.when(cp.str.starts_with("97")).then(cp.str.slice(0, 3)).otherwise(cp.str.slice(0, 2))
