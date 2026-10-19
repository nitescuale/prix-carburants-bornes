"""Pourquoi cette station est-elle chère ? La décomposition du modèle de prix du notebook 05, station par station.

Chaque station est prédite par un modèle qui ne l'a pas vue (validation croisée en cinq plis), puis sa prédiction est
décomposée avec les valeurs de Shapley de ce modèle, regroupées en grandes familles. Le résultat, un fichier JSON
d'une ligne par station, alimente la carte de l'application.

Demande lightgbm, shap et pandas (extra « analyse ») et l'historique des prix (release « historique »).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl

from carburants import analyse, enseignes, modele

# Les familles du modèle regroupées pour l'affichage : la position et la concurrence forment le « lieu » (elles se
# recouvrent, notebook 05), les services et les carburants vendus décrivent l'équipement de la station.
GROUPES = {
    "enseigne": ["Enseigne"],
    "autoroute": ["Autoroute"],
    "lieu": ["Position géographique", "Concurrence"],
    "equipement": ["Services", "Carburants vendus"],
    "anciennete": ["Ancienneté"],
}


def preparer(dossier: Path, annee: int) -> pl.DataFrame:
    """La table du modèle pour une année, construite comme dans le notebook 05 (gazole, métropole continentale)."""
    historique = dossier / "historique"
    hebdo = analyse.prix_hebdo(analyse.charger_historique(historique).filter(pl.col("carburant") == "Gazole"))
    hebdo = hebdo.with_columns(ecart=(pl.col("prix") - pl.col("prix").median().over("semaine")) * 100)

    fichier_enseignes = max((dossier / "enseignes").glob("stations_enseignes_*.parquet"))
    appariement = pl.read_parquet(fichier_enseignes).with_columns(
        enseigne=pl.when(pl.col("enseigne").is_not_null()).then(
            pl.struct("osm_marque", "osm_nom").map_elements(
                lambda r: enseignes.normaliser(r["osm_marque"], r["osm_nom"]), return_dtype=pl.Utf8
            )
        )
    ).with_columns(
        grande_surface=pl.col("enseigne").is_in(list(enseignes.GRANDES_SURFACES)).fill_null(False),
        enseigne_detaillee=pl.when(
            (pl.col("enseigne") == "TotalEnergies") & enseignes.total_access(pl.col("osm_marque"), pl.col("osm_nom"))
        ).then(pl.lit("TotalEnergies Access")).otherwise(pl.col("enseigne")),
    )
    stations = (
        analyse.stations_connues(historique).rename({"id": "station_id"})
        .filter(pl.col("latitude").is_between(42.3, 51.5), pl.col("longitude").is_between(-5.5, 8.3),
                pl.col("departement") != "20")
        .join(appariement.select("station_id", pl.col("enseigne_detaillee").alias("enseigne"), "grande_surface"),
              on="station_id", how="left")
        .with_columns(pl.col("grande_surface").fill_null(False))
    )
    description = pl.read_parquet(historique / f"stations_{annee}.parquet", columns=["id", "automate_24_24", "services"])
    vendus = modele.carburants_vendus(analyse.charger_historique(historique, [annee]))
    return modele.table_stations(hebdo, stations, description.rename({"id": "station_id"}), vendus, annee)


def expliquer(table: pl.DataFrame, plis: int = 5) -> pl.DataFrame:
    """Prédiction hors échantillon de chaque station (écart au prix médian, en centimes) et sa décomposition : la
    station moyenne (`base`) plus la contribution de chaque groupe de `GROUPES` donnent exactement la prédiction."""
    import shap
    from sklearn.model_selection import KFold

    X = modele.matrice(table)
    y = table["ecart"].to_numpy()
    prediction, pli, modeles = modele.predictions_croisees(
        X, y, KFold(plis, shuffle=True, random_state=0), renvoyer_modeles=True
    )
    base = np.zeros(len(y))
    contributions = {groupe: np.zeros(len(y)) for groupe in GROUPES}
    for k, modele_pli in enumerate(modeles):
        dans = np.flatnonzero(pli == k)
        explicateur = shap.TreeExplainer(modele_pli)
        familles = modele.contributions_par_famille(explicateur.shap_values(X.iloc[dans]), list(X.columns))
        base[dans] = float(np.ravel(explicateur.expected_value)[0])
        for groupe, noms in GROUPES.items():
            contributions[groupe][dans] = familles[noms].sum(axis=1).to_numpy()
    resultat = pl.DataFrame({
        "station_id": table["station_id"],
        "reel": y,
        "prediction": prediction,
        "base": base,
        **contributions,
    })
    somme = resultat.select(pl.sum_horizontal("base", *GROUPES)).to_series().to_numpy()
    if not np.allclose(somme, prediction, atol=1e-4):
        raise AssertionError("Les contributions ne redonnent pas la prédiction")
    return resultat


def ecrire(resultat: pl.DataFrame, annee: int, chemin: Path) -> None:
    """Le fichier JSON de l'application : colonnes puis lignes, en centimes arrondis au dixième."""
    colonnes = resultat.columns
    lignes = resultat.with_columns(pl.exclude("station_id").round(1)).rows()
    erreur = float(np.abs(resultat["reel"] - resultat["prediction"]).mean())
    contenu = {"annee": annee, "carburant": "Gazole", "erreur_moyenne": round(erreur, 2),
               "colonnes": colonnes, "lignes": lignes}
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(contenu, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
