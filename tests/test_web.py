import json
from datetime import date, datetime

import polars as pl
import pytest

from carburants import bornes, web

JOUR = date(2026, 10, 19)


def _stations():
    return pl.DataFrame({
        "id": [1, 2, 3, 4],
        "latitude": [48.85, 45.76, 16.2, 43.3],
        "longitude": [2.35, 4.83, -61.5, 5.37],
        "cp": ["75001", "69001", "97100", "13001"],
        "type_route": ["route", "autoroute", "route", "route"],
        "adresse": ["1 RUE DE RIVOLI", "A7", "x", "Quai du Port"],
        "ville": ["PARIS", "LYON", "Basse-Terre", "Marseille"],
        "automate_24_24": [True, None, False, False],
        "services": [[], [], [], []],
    })


def _prix():
    return pl.DataFrame({
        "station_id": [1, 1, 1, 2, 3, 4],
        "carburant": ["Gazole", "E10", "Gazole", "SP98", "Gazole", "Gazole"],
        # Le prix le plus récent du gazole de la station 1 n'est pas la dernière ligne
        "maj": [datetime(2026, 10, 19, 7), datetime(2026, 10, 10), datetime(2026, 10, 18, 8),
                datetime(2026, 10, 19), datetime(2026, 10, 19), datetime(2026, 10, 1)],
        "prix": [2.35, 2.10, 2.30, 2.40, 1.90, 2.20],
    })


def test_prix_du_jour():
    enseignes = pl.DataFrame({"station_id": [1], "enseigne": ["TotalEnergies"]})
    table = web.prix_du_jour(_stations(), _prix(), JOUR, enseignes)
    # Station 3 hors métropole, station 4 sans prix de moins de 8 jours
    assert table["station_id"].to_list() == [1, 2]
    premiere = table.row(0, named=True)
    # Le dernier prix déclaré du gazole ; l'E10 du 10 octobre est trop ancien
    assert premiere["Gazole"] == 2.35 and premiere["E10"] is None
    assert premiere["enseigne"] == "TotalEnergies" and premiere["automate"]
    assert premiere["ville"] == "Paris" and premiere["adresse"] == "1 Rue de Rivoli"
    assert premiere["maj"] == JOUR
    deuxieme = table.row(1, named=True)
    assert deuxieme["autoroute"] and not deuxieme["automate"] and deuxieme["SP98"] == 2.40
    assert set(web.CARBURANTS) <= set(table.columns)


def test_majuscules_adoucies():
    villes = pl.Series(["BOURG-EN-BRESSE", "LA ROCHE-SUR-YON", "Saint-Denis-lès-Bourg", None])
    resultat = pl.select(web.majuscules_adoucies(pl.lit(villes))).to_series().to_list()
    assert resultat == ["Bourg-en-Bresse", "La Roche-sur-Yon", "Saint-Denis-lès-Bourg", None]


def _points():
    return pl.DataFrame({
        "id_station": ["A", "A", "B", "C", "D", "E"],
        "operateur": ["Ionity", "Ionity", "Tesla", "Lidl", "Commune", "Ionity"],
        "implantation": ["Station"] * 6,
        "longitude": [2.0, 2.0, 2.0005, 3.0, 4.0, 5.0],
        "latitude": [47.0, 47.0, 47.0005, 47.0, 47.0, 47.0],
        "metropole": [True, True, True, True, True, True],
        "acces_reserve": [False, False, None, False, False, True],
        "puissance_kw": [350.0, 150.0, 250.0, 50.0, 22.0, 150.0],
        "classe": ["150 kW et plus", "150 kW et plus", "150 kW et plus", "50 à 150 kW", "7,4 à 22 kW", "150 kW et plus"],
        "mise_en_service": [None] * 6,
        "prix_kwh": [0.59, 0.59, None, 0.39, 0.30, 0.10],
    })


def test_sites_rapides():
    sites = web.sites_rapides(_points())
    # A et B (à 70 m) forment un site ; C est rapide ; D est lent ; E est réservé
    assert sites.height == 2
    double = sites.filter(pl.col("points_de_charge") == 3).row(0, named=True)
    assert double["operateurs"] == "Ionity · Tesla"
    assert double["puissance_max_kw"] == 350.0 and double["prix_kwh"] == 0.59


def test_sites_rapides_ignorent_les_points_lents():
    # Une station avec un point rapide à 0,59 € et deux points lents à 0,25 € : seul le point rapide compte
    points = _points().head(1).vstack(_points().head(2).tail(1)).with_columns(
        puissance_kw=pl.Series([150.0, 22.0]), prix_kwh=pl.Series([0.59, 0.25]),
    )
    points = points.vstack(points.tail(1))
    site = web.sites_rapides(points).row(0, named=True)
    assert site["points_de_charge"] == 1 and site["prix_kwh"] == 0.59 and site["puissance_max_kw"] == 150.0


def test_tarif_median_par_operateur():
    points = pl.DataFrame({
        "operateur": ["Lidl"] * 5 + ["A", "B"],
        "prix_kwh": [0.2] * 5 + [0.5, 0.6],
    })
    # La médiane des points vaudrait 0,2 ; celle des opérateurs est 0,5
    assert bornes.tarif_median(points) == (0.5, 3)
    assert bornes.tarif_median(points.with_columns(prix_kwh=None)) == (None, 0)


def test_resume_et_couts():
    prix = web.prix_du_jour(_stations(), _prix(), JOUR)
    electricite = pl.DataFrame({"semestre": ["2025-S2", "2026-S1"], "debut": [date(2025, 7, 1), date(2026, 1, 1)],
                                "prix_eur_kwh": [0.26, 0.25]})
    chiffres = web.resume(JOUR, prix, _points(), JOUR, electricite, 2)
    assert chiffres["carburants"]["Gazole"]["mediane"] == 2.35
    assert chiffres["electricite"]["domicile"] == {"prix": 0.25, "semestre": "2026-S1"}
    # Ultra-rapide : Ionity 0,59 seul tarif lisible en accès public (Tesla n'en a pas, E est réservé)
    assert chiffres["electricite"]["ultra"] == {"prix": 0.59, "operateurs": 1}
    assert chiffres["electricite"]["lente"] == {"prix": 0.3, "operateurs": 1}
    assert chiffres["cout_100km"]["Gazole"] == pytest.approx(2.35 * 5.5, abs=0.01)
    assert chiffres["cout_100km"]["domicile"] == pytest.approx(0.25 * 18, abs=0.01)


def test_construire(tmp_path):
    donnees = tmp_path / "donnees"
    photo = donnees / "prix" / "instantane" / "date=2026-10-19"
    photo.mkdir(parents=True)
    _stations().write_parquet(photo / "stations.parquet")
    _prix().write_parquet(photo / "prix.parquet")
    source = tmp_path / "site"
    source.mkdir()
    (source / "index.html").write_text("<!doctype html>")
    rapports = tmp_path / "rapports"
    (rapports / "figures").mkdir(parents=True)
    (rapports / "figures" / "01.png").write_bytes(b"png")
    (rapports / "explication_stations.json").write_text("{}")

    sortie = tmp_path / "_site"
    chiffres = web.construire(donnees, sortie, source, rapports)
    assert chiffres["stations"] == 2 and chiffres["date_bornes"] is None
    assert (sortie / "index.html").exists() and (sortie / "figures" / "01.png").exists()
    assert (sortie / "donnees" / "explication.json").exists()
    stations = json.loads((sortie / "donnees" / "stations.json").read_text())
    assert stations["date"] == "2026-10-19" and len(stations["lignes"]) == 2
    assert stations["colonnes"][:3] == ["station_id", "latitude", "longitude"]
    assert stations["lignes"][0][-1] == "2026-10-19"
    # Sans bornes, une table vide ; sans communes, pas de fichier
    recharge = json.loads((sortie / "donnees" / "recharge.json").read_text())
    assert recharge["lignes"] == [] and recharge["date"] is None and "prix_kwh" in recharge["colonnes"]
    assert not (sortie / "donnees" / "communes.json").exists()
