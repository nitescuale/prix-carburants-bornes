from datetime import date, timedelta

import numpy as np
import pandas as pd
import polars as pl
import pytest
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import KFold

from carburants import modele


def test_services_et_carburants_vendus():
    description = pl.DataFrame(
        {
            "station_id": [1, 2, 3],
            "automate_24_24": [True, None, False],
            "services": [["Boutique alimentaire", "Wifi", "Station de gonflage"], None, []],
        }
    )
    resultat = modele.services(description).sort("station_id")
    assert resultat["n_services"].to_list() == [3, 0, 0]
    assert resultat["boutique"].to_list() == [True, False, False]
    assert resultat["wifi"].to_list() == [True, False, False]
    assert resultat["automate_24_24"].to_list() == [True, False, False]

    prix = pl.DataFrame({"station_id": [1, 1, 2], "carburant": ["Gazole", "E85", "SP98"]})
    vendus = modele.carburants_vendus(prix).sort("station_id")
    assert vendus["vend_e85"].to_list() == [True, False]
    assert vendus["vend_sp98"].to_list() == [False, True]


def test_regrouper_enseignes():
    table = pl.DataFrame({"enseigne": ["Esso", None, "Rare"]})
    assert table.select(modele.regrouper_enseignes(pl.col("enseigne"), ["Esso"])).to_series().to_list() == [
        "Esso", "Inconnue", "Autre"
    ]


def test_predictions_croisees_retrouvent_un_effet_simple():
    rng = np.random.default_rng(0)
    n = 2000
    table = pl.DataFrame(
        {
            "enseigne": rng.choice(["A", "B"], n).tolist(),
            "autoroute": rng.random(n) < 0.1,
            "latitude": rng.uniform(43, 50, n),
        }
    )
    y = np.where(table["enseigne"] == "A", -2.0, 3.0) + 15 * table["autoroute"].to_numpy() + rng.normal(0, 0.5, n)
    X = modele.matrice(table, ["enseigne", "autoroute", "latitude"])
    assert X["enseigne"].dtype == "category"
    prediction = modele.predictions_croisees(X, y, KFold(5, shuffle=True, random_state=0), n_estimators=200)
    assert not np.isnan(prediction).any()
    assert modele.scores(y, prediction)["r2"] > 0.95


def test_contributions_par_famille_additionnent_avant_la_valeur_absolue():
    valeurs = np.array([[1.0, -1.0, 2.0], [0.5, 0.5, 0.0]])
    familles = {"Concurrence": ["a", "b"], "Enseigne": ["c"]}
    resultat = modele.contributions_par_famille(valeurs, ["a", "b", "c"], familles)
    assert resultat["Concurrence"].tolist() == [0.0, 1.0]
    assert resultat["Enseigne"].tolist() == [2.0, 0.0]


def test_remise_fin_de_semaine():
    assert modele.remise_fin_de_semaine(date(2022, 3, 21)) == 0
    assert modele.remise_fin_de_semaine(date(2022, 3, 28)) == 0.18  # remise du vendredi 1er avril
    assert modele.remise_fin_de_semaine(date(2022, 9, 5)) == 0.30
    assert modele.remise_fin_de_semaine(date(2022, 12, 26)) == 0.10  # fin le dimanche 1er janvier
    assert modele.remise_fin_de_semaine(date(2023, 1, 2)) == 0


def _serie(debut, n, prix, cout):
    return pl.DataFrame({"semaine": [debut + timedelta(weeks=k) for k in range(n)], "prix": prix, "cout": cout})


def test_table_prevision_retire_la_remise():
    debut = date(2022, 3, 7)
    serie = _serie(debut, 6, [1.80, 1.80, 1.80, 1.62, 1.62, 1.62], [0.6] * 6)
    table = modele.table_prevision(serie, marge_semaines=2)
    # La baisse de 18 centimes du 1er avril est la remise : le prix hors remise ne bouge pas
    assert table["prix"].tolist() == pytest.approx([1.80] * 6)
    assert table["cible"].iloc[:5].tolist() == pytest.approx([0] * 5)
    assert table["variation_remise"].iloc[2] == pytest.approx(0.18)


def test_prevision_glissante_bat_la_persistance_quand_le_cout_annonce_le_prix():
    rng = np.random.default_rng(1)
    n = 300
    cout = np.cumsum(rng.normal(0, 0.02, n)) + 0.6
    # Le prix suit le coût avec une semaine de retard
    prix = 1.0 + np.concatenate([[cout[0]], cout[:-1]])
    serie = _serie(date(2010, 1, 4), n, prix.tolist(), cout.tolist())
    table = modele.table_prevision(serie, marge_semaines=10)
    previsions = modele.prevision_glissante(table, date(2013, 1, 7), {"lineaire": LinearRegression})
    assert len(previsions) > 50
    erreur = (previsions["reel"] - previsions["lineaire"]).abs().mean()
    assert erreur < 1e-6 < (previsions["reel"] - previsions["persistance"]).abs().mean()
    assert isinstance(previsions, pd.DataFrame)


def test_remise_aux_changements_de_montant():
    assert modele.remise_fin_de_semaine(date(2022, 8, 29)) == 0.30  # passage à 30 centimes le jeudi 1er septembre
    assert modele.remise_fin_de_semaine(date(2022, 11, 14)) == 0.10  # passage à 10 centimes le mercredi 16 novembre


def test_matrice_ordre_et_booleens():
    table = pl.DataFrame({"enseigne": ["A", "B"], "autoroute": [True, False], "latitude": [45.0, 46.0]})
    X = modele.matrice(table, ["latitude", "enseigne", "autoroute"])
    assert list(X.columns) == ["latitude", "enseigne", "autoroute"]
    assert X["autoroute"].tolist() == [1.0, 0.0]


def test_contributions_refuse_une_colonne_hors_famille():
    with pytest.raises(ValueError):
        modele.contributions_par_famille(np.zeros((1, 2)), ["a", "b"], {"F": ["a"]})


def test_predictions_croisees_par_groupe_et_dependance_partielle():
    from sklearn.model_selection import GroupKFold

    rng = np.random.default_rng(2)
    n = 600
    table = pl.DataFrame({"enseigne": rng.choice(["A", "B"], n).tolist(), "latitude": rng.uniform(43, 50, n)})
    y = np.where(table["enseigne"] == "A", -2.0, 3.0) + rng.normal(0, 0.3, n)
    X = modele.matrice(table, ["enseigne", "latitude"])
    groupes = rng.integers(0, 10, n)
    _, pli, modeles = modele.predictions_croisees(
        X, y, GroupKFold(5), groupes, renvoyer_modeles=True, n_estimators=100
    )
    assert len(modeles) == 5 and (pli >= 0).all()
    # Chaque groupe est prédit par un seul pli
    assert all(len(set(pli[groupes == g])) == 1 for g in range(10))
    ecart = modele.dependance_partielle(modeles[0], X, "enseigne", "B") - modele.dependance_partielle(modeles[0], X, "enseigne", "A")
    assert ecart == pytest.approx(5, abs=0.5)


def test_prevision_glissante_ne_regarde_pas_l_avenir():
    rng = np.random.default_rng(3)
    n = 200
    cout = np.cumsum(rng.normal(0, 0.02, n)) + 0.6
    prix = 1.0 + np.concatenate([[cout[0]], cout[:-1]]) + rng.normal(0, 0.005, n)
    serie = _serie(date(2010, 1, 4), n, prix.tolist(), cout.tolist())
    table = modele.table_prevision(serie, marge_semaines=10)
    debut = date(2012, 1, 2)
    reference = modele.prevision_glissante(table, debut, {"lineaire": LinearRegression}, reestimation=1)
    # On bouleverse les prix après une date : les prévisions faites avant ne doivent pas changer
    coupure = date(2012, 7, 2)
    modifie = table.copy()
    apres = modifie["semaine"] > coupure
    modifie.loc[apres, ["prix", "cible", "cout_0", "prix_0"]] += 5.0
    autre = modele.prevision_glissante(modifie, debut, {"lineaire": LinearRegression}, reestimation=1)
    avant = reference["semaine"] < coupure
    assert np.allclose(reference.loc[avant, "lineaire"], autre.loc[avant, "lineaire"])


def test_prevision_glissante_sous_ensemble_de_variables():
    rng = np.random.default_rng(4)
    n = 150
    cout = np.cumsum(rng.normal(0, 0.02, n)) + 0.6
    prix = 1.0 + np.concatenate([[cout[0]], cout[:-1]])
    table = modele.table_prevision(_serie(date(2010, 1, 4), n, prix.tolist(), cout.tolist()), marge_semaines=10)
    previsions = modele.prevision_glissante(table, date(2011, 6, 6), {"cout": (LinearRegression, ["cout_0"])})
    assert (previsions["reel"] - previsions["cout"]).abs().max() < 1e-6


def test_diebold_mariano():
    rng = np.random.default_rng(5)
    bonnes, mauvaises = rng.normal(0, 1, 500), rng.normal(0, 2, 500)
    statistique, p = modele.diebold_mariano(bonnes, mauvaises)
    assert statistique < 0 and p < 0.001
    assert abs(modele.diebold_mariano(bonnes, rng.normal(0, 1, 500))[0]) < 4


def test_table_stations():
    semaines = [date(2025, 1, 6) + timedelta(weeks=k) for k in range(30)]
    hebdo = pl.DataFrame(
        {
            "station_id": [1] * 30 + [2] * 30 + [3] * 5,
            "semaine": semaines * 2 + semaines[:5],
            "ecart": [1.0] * 30 + [-1.0] * 30 + [0.0] * 5,
        }
    )
    stations = pl.DataFrame(
        {
            "station_id": [1, 2, 3],
            "latitude": [46.5, 46.51, 46.55],
            "longitude": [2.0, 2.0, 2.0],
            "type_route": ["route", "autoroute", "route"],
            "enseigne": ["TotalEnergies", None, "E.Leclerc"],
            "grande_surface": [False, False, True],
        }
    )
    description = pl.DataFrame({"station_id": [1, 2], "automate_24_24": [True, False], "services": [["Wifi"], None]})
    vendus = pl.DataFrame({"station_id": [1], "vend_e10": [True]})
    table = modele.table_stations(hebdo, stations, description, vendus, 2025, enseignes_min=1)
    # La station 3 publie trop peu, mais compte comme concurrente
    assert table["station_id"].to_list() == [1, 2]
    assert table["voisins_10km"].to_list() == [2, 2]
    assert table["enseigne"].to_list() == ["TotalEnergies", "Inconnue"]
    assert table["autoroute"].to_list() == [False, True]
    assert table["vend_e10"].to_list() == [True, False]
    assert table["anciennete"].to_list() == [0.0, 0.0]
    assert table["gms_km"].to_list() == pytest.approx([5.56, 4.45], abs=0.05)
