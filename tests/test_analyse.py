from datetime import date, datetime

import polars as pl
import pytest

from carburants import analyse


def _prix(lignes):
    return pl.DataFrame(
        lignes,
        schema={"station_id": pl.Int64, "carburant": pl.Utf8, "maj": pl.Datetime("us"), "prix": pl.Float64},
        orient="row",
    )


def test_prix_hebdo_garde_le_dernier_prix_de_la_semaine_et_le_prolonge():
    prix = _prix(
        [
            (1, "Gazole", datetime(2025, 1, 6, 8), 1.70),  # lundi
            (1, "Gazole", datetime(2025, 1, 9, 8), 1.72),  # jeudi, même semaine
            (1, "Gazole", datetime(2025, 1, 27, 8), 1.75),  # trois semaines plus tard
            (2, "Gazole", datetime(2025, 1, 27, 9), 1.80),
        ]
    )
    hebdo = analyse.prix_hebdo(prix).filter(station_id=1)
    assert hebdo["semaine"].to_list() == [date(2025, 1, 6), date(2025, 1, 13), date(2025, 1, 20), date(2025, 1, 27)]
    assert hebdo["prix"].to_list() == [1.72, 1.72, 1.72, 1.75]
    assert hebdo["publie"].to_list() == [True, False, False, True]


def test_prix_hebdo_arrete_la_prolongation():
    prix = _prix(
        [
            (1, "Gazole", datetime(2025, 1, 6, 8), 1.70),
            (2, "Gazole", datetime(2025, 3, 3, 8), 1.80),  # fixe la fin des données
        ]
    )
    hebdo = analyse.prix_hebdo(prix, validite_semaines=2).filter(station_id=1)
    assert hebdo["semaine"].to_list() == [date(2025, 1, 6), date(2025, 1, 13)]


def test_prix_hebdo_ne_depasse_pas_la_fin_des_donnees():
    prix = _prix([(1, "Gazole", datetime(2025, 1, 6, 8), 1.70), (1, "E10", datetime(2025, 1, 13, 8), 1.80)])
    hebdo = analyse.prix_hebdo(prix)
    assert hebdo["semaine"].max() == date(2025, 1, 13)
    assert hebdo.filter(carburant="Gazole").height == 2


def test_statistiques_hebdo():
    hebdo = pl.DataFrame(
        {
            "carburant": ["Gazole"] * 3,
            "semaine": [date(2025, 1, 6)] * 3,
            "prix": [1.6, 1.7, 1.8],
        }
    )
    stats = analyse.statistiques_hebdo(hebdo).row(0, named=True)
    assert stats["stations"] == 3
    assert stats["mediane"] == pytest.approx(1.7)


def test_charger_historique_filtre_les_prix_aberrants(tmp_path):
    _prix([(1, "Gazole", datetime(2007, 1, 2), 1.1), (1, "Gazole", datetime(2007, 1, 3), 100.0)]).write_parquet(
        tmp_path / "prix_2007.parquet"
    )
    _prix([(1, "Gazole", datetime(2008, 1, 2), 1.2)]).write_parquet(tmp_path / "prix_2008.parquet")
    assert analyse.charger_historique(tmp_path).collect()["prix"].to_list() == [1.1, 1.2]
    assert analyse.charger_historique(tmp_path, annees=[2008]).collect().height == 1


def test_stations_connues_garde_la_description_la_plus_recente(tmp_path):
    colonnes = {"id": pl.Int64, "cp": pl.Utf8, "type_route": pl.Utf8, "latitude": pl.Float64, "longitude": pl.Float64}
    pl.DataFrame([(1, "75001", "route", 48.0, 2.0)], schema=colonnes, orient="row").write_parquet(tmp_path / "stations_2010.parquet")
    pl.DataFrame([(1, "97411", "route", -21.0, 55.0), (2, "20000", "route", 41.9, 8.7)], schema=colonnes, orient="row").write_parquet(
        tmp_path / "stations_2020.parquet"
    )
    stations = analyse.stations_connues(tmp_path)
    assert stations["departement"].to_list() == ["974", "20"]
