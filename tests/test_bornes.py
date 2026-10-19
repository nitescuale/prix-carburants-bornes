from datetime import date

import numpy as np
import polars as pl
import pytest

from carburants import bornes


def test_booleen_puissance_et_classe():
    t = pl.DataFrame({"b": ["TRUE", "false", "1", "0", " Oui", "?", None], "p": ["22.08", "22000", "0", "1242", "50", "150", "x"]})
    resultat = t.select(
        b=bornes.booleen(pl.col("b")), p=bornes.puissance_kw(pl.col("p"))
    ).with_columns(classe=bornes.classe_puissance(pl.col("p")))
    assert resultat["b"].to_list() == [True, False, True, False, True, None, None]
    assert resultat["p"].to_list() == [22.08, 22.0, None, 1.242, 50.0, 150.0, None]
    assert resultat["classe"].to_list() == [
        "7,4 à 22 kW", "7,4 à 22 kW", None, "jusqu'à 7,4 kW", "50 à 150 kW", "150 kW et plus", None,
    ]


@pytest.mark.parametrize(
    ("tarif", "attendu"),
    [
        ("0,55€/kWh", 0.55),
        ("entre 07:00 et 22:00 : 0.3333€ par kwh de charge, 5.0€ par heure", 0.3333),
        ("0,45 € TTC le kWh", 0.45),
        ('{"fixedPrice":0,"energyPrice":0.39,"matrix":[]}', 0.39),
        ("https://belib.paris", None),
        ("PAYANT", None),
        ("HPC 49cts/Kwh", 0.49),
        ("0,75 e/ kwh", 0.75),
        ("AC 36cts/KWh, DC 0,59 €/kWh", 0.59),
        ("45€/kWh", None),
        (None, None),
    ],
)
def test_prix_kwh(tarif, attendu):
    assert bornes.prix_kwh(tarif) == attendu


def _irve(**colonnes):
    base = {
        "id_pdc_itinerance": ["A1", "A1", "B1"],
        "id_pdc_local": [None, None, None],
        "id_station_itinerance": ["SA", "SA", None],
        "id_station_local": [None, None, "SB"],
        "nom_operateur": ["vieux", "neuf", "x"],
        "nom_amenageur": ["a", "a", "b"],
        "nom_enseigne": ["e", "e", "f"],
        "implantation_station": ["Voirie", "Parking privé ŕ usage public", "Voirie"],
        "condition_acces": ["Accčs libre", "Accès libre", "Accès réservé"],
        "code_insee_commune": ["75056", "75056", None],
        "coordonneesXY": ["[2.35,48.85]", "[2.35,48.85]", "[ -61.5 , 16.2 ]"],
        "consolidated_longitude": ["2.351", None, None],
        "consolidated_latitude": ["48.851", None, None],
        "puissance_nominale": ["22", "150", "7"],
        "date_mise_en_service": ["2020-05-01", "2020-05-01", "2031-01-01"],
        "gratuit": ["false", "False", None],
        "tarification": [None, "0,49 €/kWh", None],
        "date_maj": ["2021-01-01", "2026-10-01", "2026-01-01"],
        "last_modified": ["2021-01-01T00:00:00", "2026-10-01T00:00:00", "2026-01-01T00:00:00"],
        "datagouv_organization_or_owner": ["izivia", "izivia", "sdem"],
    }
    base.update(colonnes)
    return pl.DataFrame(base)


def test_nettoyer_garde_la_declaration_la_plus_recente():
    points = bornes.nettoyer(_irve(), aujourd_hui=date(2026, 10, 19)).sort("id_pdc")
    assert points["id_pdc"].to_list() == ["A1", "B1"]
    a, b = points.row(0, named=True), points.row(1, named=True)
    assert a["operateur"] == "neuf" and a["puissance_kw"] == 150 and a["classe"] == "150 kW et plus"
    assert a["implantation"] == "Parking privé à usage public"
    assert a["prix_kwh"] == 0.49 and a["gratuit"] is False and a["metropole"]
    # Coordonnées déclarées à défaut des coordonnées consolidées
    assert (a["longitude"], a["latitude"]) == (2.35, 48.85)
    assert a["acces_reserve"] is False and b["acces_reserve"] is True
    assert b["id_station"] == "SB" and b["longitude"] == -61.5 and not b["metropole"]
    assert b["mise_en_service"] is None  # date dans le futur


def test_nettoyer_prefere_l_operateur_et_ignore_non_concerne():
    irve = _irve(
        id_pdc_itinerance=["A1", "A1", "Non concerné"],
        id_pdc_local=[None, None, None],
        id_station_itinerance=["SA", "SA", "Non concerné"],
        id_station_local=[None, None, None],
        puissance_nominale=["22", "0", "7"],
        date_maj=["2025-01-01", "2026-10-18", "2026-01-01"],
        datagouv_organization_or_owner=["izivia", "eco-movement", "sdem"],
    )
    deux = pl.concat([irve, irve.tail(1)])  # deux points « Non concerné » distincts
    points = bornes.nettoyer(deux, aujourd_hui=date(2026, 10, 19))
    assert points.height == 3
    a = points.filter(id_pdc="A1").row(0, named=True)
    assert a["puissance_kw"] == 22  # la déclaration de l'opérateur, pas celle de l'agrégateur plus récente
    sans_id = points.filter(pl.col("id_pdc").str.starts_with("ligne-"))
    assert sans_id.height == 2 and sans_id["id_station"].n_unique() == 2


def test_sites():
    table = pl.DataFrame({"latitude": [48.85, 48.8505, 48.851, 48.9], "longitude": [2.35, 2.35, 2.35, 2.35]})
    numeros = bornes.sites(table, rayon_km=0.08)
    assert numeros[0] == numeros[1] == numeros[2] != numeros[3]  # de proche en proche


def test_stations_et_distances():
    points = bornes.nettoyer(_irve(), aujourd_hui=date(2026, 10, 19))
    stations = bornes.stations_recharge(points)
    assert stations["id_station"].to_list() == ["SA", "SB"]
    assert stations["points_de_charge"].to_list() == [1, 1]

    origines = pl.DataFrame({"latitude": [48.85, 48.95], "longitude": [2.35, 2.35]})
    cibles = pl.DataFrame({"latitude": [48.85], "longitude": [2.35]})
    distances = bornes.distance_plus_proche_km(origines, cibles)
    assert distances[0] == pytest.approx(0, abs=1e-9)
    assert distances[1] == pytest.approx(11.12, abs=0.01)
    assert np.isnan(bornes.distance_plus_proche_km(origines, cibles.head(0))).all()

    parts = bornes.part_population_au_dela(np.array([1.0, 12.0, np.nan]), np.array([100, 300, 50]), [5, 20])
    assert parts == {5: 75.0, 20: 0.0}
