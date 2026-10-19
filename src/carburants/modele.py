"""Modèle du prix d'une station (gradient boosting) et prévision du prix national à une semaine.

Le modèle de station explique l'écart moyen d'une station au prix médian national par ce que l'on sait d'elle : son
enseigne, sa position, ses services, les carburants qu'elle vend et ses concurrents. Les contributions de chaque
caractéristique se lisent avec les valeurs de Shapley (SHAP), regroupées par famille.

Ce module demande lightgbm (extra « modele ») ; shap n'est utilisé que par les notebooks.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import numpy as np
import pandas as pd
import polars as pl

# Services déclarés par les stations retenus comme caractéristiques (libellé du flux → nom de colonne)
SERVICES = {
    "Boutique alimentaire": "boutique",
    "Lavage automatique": "lavage_auto",
    "Lavage manuel": "lavage_manuel",
    "Restauration à emporter": "restauration_emporter",
    "Restauration sur place": "restauration",
    "Toilettes publiques": "toilettes",
    "Piste poids lourds": "poids_lourds",
    "DAB (Distributeur automatique de billets)": "dab",
    "Bornes électriques": "bornes",
    "Wifi": "wifi",
    "Services réparation / entretien": "atelier",
    "Location de véhicule": "location",
}
CARBURANTS_OFFERTS = ["SP95", "SP98", "E10", "E85", "GPLc"]

FAMILLES = {
    "Enseigne": ["enseigne"],
    "Autoroute": ["autoroute"],
    "Position géographique": ["latitude", "longitude"],
    "Carburants vendus": [f"vend_{c.lower()}" for c in CARBURANTS_OFFERTS],
    "Services": ["automate_24_24", "n_services", *SERVICES.values()],
    "Concurrence": ["voisins_1km", "voisins_3km", "voisins_5km", "voisins_10km", "plus_proche_km", "gms_km", "total_km"],
    "Ancienneté": ["anciennete"],
}
COLONNES = [colonne for colonnes in FAMILLES.values() for colonne in colonnes]

PARAMETRES = {
    "n_estimators": 600, "learning_rate": 0.03, "num_leaves": 31, "min_child_samples": 20, "subsample": 0.8,
    "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0, "random_state": 0, "deterministic": True,
    "force_col_wise": True, "num_threads": 4, "verbose": -1,
}  # fixés a priori, sans recherche sur la validation croisée

# Régions de métropole continentale (codes de département)
REGIONS = {
    "Auvergne-Rhône-Alpes": ["01", "03", "07", "15", "26", "38", "42", "43", "63", "69", "73", "74"],
    "Bourgogne-Franche-Comté": ["21", "25", "39", "58", "70", "71", "89", "90"],
    "Bretagne": ["22", "29", "35", "56"],
    "Centre-Val de Loire": ["18", "28", "36", "37", "41", "45"],
    "Grand Est": ["08", "10", "51", "52", "54", "55", "57", "67", "68", "88"],
    "Hauts-de-France": ["02", "59", "60", "62", "80"],
    "Île-de-France": ["75", "77", "78", "91", "92", "93", "94", "95"],
    "Normandie": ["14", "27", "50", "61", "76"],
    "Nouvelle-Aquitaine": ["16", "17", "19", "23", "24", "33", "40", "47", "64", "79", "86", "87"],
    "Occitanie": ["09", "11", "12", "30", "31", "32", "34", "46", "48", "65", "66", "81", "82"],
    "Pays de la Loire": ["44", "49", "53", "72", "85"],
    "Provence-Alpes-Côte d'Azur": ["04", "05", "06", "13", "83", "84"],
}


def region(departement: pl.Expr) -> pl.Expr:
    """La région d'un département de métropole continentale (null ailleurs)."""
    correspondance = {dep: nom for nom, deps in REGIONS.items() for dep in deps}
    return departement.replace_strict(correspondance, default=None, return_dtype=pl.Utf8)


def services(description: pl.DataFrame) -> pl.DataFrame:
    """Les services d'une station en colonnes booléennes, à partir de la liste `services` du flux."""
    return description.select(
        "station_id",
        automate_24_24=pl.col("automate_24_24").fill_null(False),
        n_services=pl.col("services").list.len().fill_null(0),
        **{nom: pl.col("services").list.contains(libelle).fill_null(False) for libelle, nom in SERVICES.items()},
    )


def carburants_vendus(prix: pl.DataFrame | pl.LazyFrame) -> pl.DataFrame:
    """Pour chaque station, les carburants pour lesquels elle a publié au moins un prix."""
    return (
        prix.lazy()
        .group_by("station_id")
        .agg(**{f"vend_{c.lower()}": (pl.col("carburant") == c).any() for c in CARBURANTS_OFFERTS})
        .collect()
    )


def regrouper_enseignes(enseigne: pl.Expr, frequentes: list[str]) -> pl.Expr:
    """Les enseignes fréquentes telles quelles, les autres sous « Autre », les inconnues sous « Inconnue »."""
    return (
        pl.when(enseigne.is_null()).then(pl.lit("Inconnue"))
        .when(enseigne.is_in(frequentes)).then(enseigne)
        .otherwise(pl.lit("Autre"))
    )


def table_stations(
    hebdo: pl.DataFrame,
    stations: pl.DataFrame,
    description: pl.DataFrame,
    vendus: pl.DataFrame,
    annee: int,
    semaines_min: int = 26,
    enseignes_min: int = 40,
) -> pl.DataFrame:
    """Une ligne par station active dans l'année : écart moyen au prix médian (`ecart`) et caractéristiques.

    - `hebdo` : prix hebdomadaires avec leur écart au prix médian (`station_id`, `semaine`, `ecart`), toutes années ;
    - `stations` : position, département, `type_route`, `enseigne` (détaillée) et `grande_surface` de chaque station ;
    - `description` : `station_id`, `automate_24_24` et `services` de l'année (fichier stations_AAAA) ;
    - `vendus` : sortie de `carburants_vendus` pour l'année.

    Les concurrents sont comptés parmi toutes les stations qui publient dans l'année ; seules les stations qui
    publient au moins `semaines_min` semaines sont gardées. L'ancienneté (années depuis le premier prix publié, à
    partir de 2007) vaut 0 pour une station apparue dans l'année.
    """
    from carburants import concurrence

    actives = (
        hebdo.filter(pl.col("semaine").dt.year() == annee)
        .group_by("station_id")
        .agg(ecart=pl.col("ecart").mean(), semaines=pl.len())
        .join(stations, on="station_id", how="inner")
    )
    actives = concurrence.voisins(actives, [1, 3, 5, 10])
    actives = actives.with_columns(
        gms_km=concurrence.distance_plus_proche(actives, actives.filter(pl.col("grande_surface"))),
        total_km=concurrence.distance_plus_proche(actives, actives.filter(pl.col("enseigne") == "TotalEnergies")),
    )
    debuts = hebdo.group_by("station_id").agg(debut=pl.col("semaine").min())
    table = (
        actives.join(services(description), on="station_id", how="left")
        .join(vendus, on="station_id", how="left")
        .join(debuts, on="station_id")
        .with_columns(
            pl.col("^vend_.*$", "automate_24_24", *SERVICES.values()).fill_null(False),
            pl.col("n_services").fill_null(0),
            anciennete=((pl.date(annee, 1, 1) - pl.col("debut")).dt.total_days() / 365.25).clip(lower_bound=0),
            autoroute=pl.col("type_route") == "autoroute",
            enseigne_osm=pl.col("enseigne"),
        )
        .filter(pl.col("semaines") >= semaines_min)
    )
    comptes = table["enseigne_osm"].value_counts()
    frequentes = comptes.filter(pl.col("count") >= enseignes_min)["enseigne_osm"].drop_nulls().to_list()
    return table.with_columns(enseigne=regrouper_enseignes(pl.col("enseigne_osm"), frequentes)).sort("station_id")


def matrice(table: pl.DataFrame, colonnes: list[str] = COLONNES) -> pd.DataFrame:
    """Les caractéristiques au format attendu par LightGBM : nombres, booléens en 0/1, enseigne catégorielle."""
    X = pd.DataFrame({c: table[c].cast(pl.Float64).to_list() for c in colonnes if c != "enseigne"})
    if "enseigne" in colonnes:
        X["enseigne"] = pd.Categorical(table["enseigne"].to_list())
    return X[colonnes]


def predictions_croisees(X: pd.DataFrame, y: np.ndarray, plis, groupes=None, renvoyer_modeles: bool = False, **parametres):
    """Prédictions hors échantillon : chaque station est prédite par un modèle qui ne l'a pas vue.

    Avec `renvoyer_modeles`, renvoie aussi le numéro du pli qui a prédit chaque station, et les modèles des plis.
    """
    import lightgbm as lgb

    prediction = np.full(len(y), np.nan)
    pli = np.full(len(y), -1)
    modeles = []
    for k, (apprentissage, test) in enumerate(plis.split(X, y, groupes)):
        modele = lgb.LGBMRegressor(**{**PARAMETRES, **parametres}).fit(X.iloc[apprentissage], y[apprentissage])
        prediction[test] = modele.predict(X.iloc[test])
        pli[test] = k
        modeles.append(modele)
    return (prediction, pli, modeles) if renvoyer_modeles else prediction


def dependance_partielle(modele, X: pd.DataFrame, colonne: str, valeur) -> float:
    """Prédiction moyenne si toutes les stations prenaient la valeur `valeur` pour `colonne`, le reste inchangé."""
    modifiee = X.copy()
    if isinstance(X[colonne].dtype, pd.CategoricalDtype):
        modifiee[colonne] = pd.Categorical([valeur] * len(X), categories=X[colonne].cat.categories)
    else:
        modifiee[colonne] = valeur
    return float(modele.predict(modifiee).mean())


def scores(y: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    """Erreur absolue moyenne et part de la variance expliquée (R²)."""
    residus = y - prediction
    return {"mae": float(np.abs(residus).mean()), "r2": float(1 - (residus**2).sum() / ((y - y.mean()) ** 2).sum())}


def contributions_par_famille(valeurs: np.ndarray, colonnes: list[str], familles: dict[str, list[str]] = FAMILLES) -> pd.DataFrame:
    """Somme des valeurs SHAP de chaque famille de caractéristiques, station par station.

    Additionner avant de prendre la valeur absolue évite de compter deux fois des caractéristiques corrélées qui se
    compensent (par exemple le nombre de concurrents à 3 et à 5 km).
    """
    membres = [c for colonnes_famille in familles.values() for c in colonnes_famille]
    if sorted(membres) != sorted(colonnes):
        raise ValueError("Les familles doivent couvrir chaque colonne exactement une fois")
    indices = {c: i for i, c in enumerate(colonnes)}
    return pd.DataFrame({famille: valeurs[:, [indices[c] for c in cols]].sum(axis=1) for famille, cols in familles.items()})


# Remise de l'État en 2022, en euros par litre TTC, avec ses dates d'application à la pompe
REMISES_TTC_2022 = [
    (date(2022, 4, 1), date(2022, 8, 31), 0.18),
    (date(2022, 9, 1), date(2022, 11, 15), 0.30),
    (date(2022, 11, 16), date(2022, 12, 31), 0.10),
]


def remise_fin_de_semaine(semaine: date) -> float:
    """La remise en vigueur le samedi de la semaine qui commence le lundi `semaine` (0 hors de 2022).

    Le samedi est le dernier jour où presque toutes les stations mettent à jour leurs prix : la fin de la remise,
    le dimanche 1er janvier 2023, n'apparaît dans les prix que la semaine suivante.
    """
    samedi = semaine + timedelta(days=5)
    return next((montant for debut, fin, montant in REMISES_TTC_2022 if debut <= samedi <= fin), 0.0)


def table_prevision(serie: pl.DataFrame, retards_cout: int = 4, retards_prix: int = 3, marge_semaines: int = 52) -> pd.DataFrame:
    """Les variables de la prévision à une semaine, à partir d'une série hebdomadaire `semaine`, `prix`, `cout`.

    `prix` est le prix affiché à la fin de la semaine et `cout` le cours du pétrole connu à cette date. La remise
    de 2022, annoncée à l'avance, est ajoutée au prix pour travailler sur un prix hors remise. La cible est la
    variation du prix hors remise la semaine suivante ; `variation_remise` permet de revenir au prix affiché.
    Une semaine manquante reprend la valeur de la précédente (jamais celle d'une semaine future).
    """
    grille = pl.DataFrame({"semaine": pl.date_range(serie["semaine"].min(), serie["semaine"].max(), "1w", eager=True)})
    x = grille.join(serie.select("semaine", "prix", "cout"), on="semaine", how="left").with_columns(
        pl.col("prix", "cout").forward_fill()
    )
    t = pd.DataFrame({"semaine": x["semaine"].to_list(), "prix_affiche": x["prix"].to_list(), "cout": x["cout"].to_list()})
    t["remise"] = [remise_fin_de_semaine(s) for s in t["semaine"]]
    t["prix"] = t["prix_affiche"] + t["remise"]
    dp, dc = t["prix"].diff(), t["cout"].diff()
    for k in range(retards_cout):
        t[f"cout_{k}"] = dc.shift(k)
    for k in range(retards_prix):
        t[f"prix_{k}"] = dp.shift(k)
    marge = t["prix"] - t["cout"]
    t["marge"] = marge - marge.rolling(marge_semaines).mean()
    t["cible"] = t["prix"].shift(-1) - t["prix"]
    t["variation_remise"] = t["remise"].shift(-1) - t["remise"]
    return t


def variables_prevision(table: pd.DataFrame) -> list[str]:
    """Toutes les variables explicatives de `table_prevision` : variations retardées du coût et du prix, marge."""
    return [c for c in table.columns if c.startswith(("cout_", "prix_")) and c != "prix_affiche"] + ["marge"]


def prevision_glissante(
    table: pd.DataFrame, debut: date, modeles: dict[str, Callable | tuple[Callable, list[str]]], reestimation: int = 13
) -> pd.DataFrame:
    """Prévisions à une semaine en conditions réelles : chaque semaine à partir de `debut`, on prévoit la suivante
    avec des modèles estimés sur le seul passé (réestimés toutes les `reestimation` semaines).

    `modeles` associe un nom à une fonction qui renvoie un estimateur scikit-learn neuf, ou à un couple (fonction,
    variables) pour n'utiliser qu'une partie des variables. La persistance (pas de variation hors remise) sert de
    référence. Les erreurs sont en euros par litre, sur le prix hors remise.
    """
    toutes = variables_prevision(table)
    specs = {nom: m if isinstance(m, tuple) else (m, toutes) for nom, m in modeles.items()}
    utilisable = table.dropna(subset=toutes).reset_index(drop=True)
    lignes, estimes = [], {}
    a_prevoir = utilisable.index[(utilisable["semaine"] >= debut) & utilisable["cible"].notna()]
    for rang, i in enumerate(a_prevoir):
        if rang % reestimation == 0:
            # La ligne i - 1 a pour cible la variation de la semaine i, connue à la fin de la semaine i
            passe = utilisable.loc[: i - 1].dropna(subset=["cible"])
            estimes = {nom: (fabrique().fit(passe[v], passe["cible"]), v) for nom, (fabrique, v) in specs.items()}
        ligne = {"semaine": utilisable.loc[i, "semaine"], "reel": utilisable.loc[i, "cible"], "persistance": 0.0}
        for nom, (estime, v) in estimes.items():
            ligne[nom] = float(estime.predict(utilisable.loc[[i], v])[0])
        lignes.append(ligne)
    return pd.DataFrame(lignes)


def diebold_mariano(erreurs_a: np.ndarray, erreurs_b: np.ndarray, retards: int = 4) -> tuple[float, float]:
    """Test de Diebold et Mariano sur les erreurs absolues : (statistique, p-valeur bilatérale).

    Une statistique négative signifie que la prévision A se trompe moins que la B. La variance de l'écart moyen est
    estimée avec une correction de Newey et West (`retards` semaines), car les erreurs successives se ressemblent.
    """
    from scipy.stats import norm

    d = np.abs(np.asarray(erreurs_a)) - np.abs(np.asarray(erreurs_b))
    n = len(d)
    centre = d - d.mean()
    variance = centre @ centre / n
    for k in range(1, retards + 1):
        variance += 2 * (1 - k / (retards + 1)) * (centre[k:] @ centre[:-k]) / n
    statistique = d.mean() / np.sqrt(variance / n)
    return float(statistique), float(2 * norm.sf(abs(statistique)))
