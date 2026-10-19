import polars as pl
import pytest

from carburants import enseignes


@pytest.mark.parametrize(
    ("marque", "nom", "attendu"),
    [
        ("TotalEnergies", None, "TotalEnergies"),
        ("Total Access", None, "TotalEnergies"),
        ("E.Leclerc", None, "E.Leclerc"),
        ("Super U", None, "Système U"),
        ("Hyper U", None, "Système U"),
        ("Intermarché Super", None, "Intermarché"),
        ("BP", None, "BP"),
        ("Esso Express", None, "Esso"),
        (None, "Station Avia du Lac", "Avia"),
        (None, "Garage Dupont", None),
        ("Petrovex", None, "Petrovex"),
        (None, None, None),
    ],
)
def test_normaliser(marque, nom, attendu):
    assert enseignes.normaliser(marque, nom) == attendu


def test_elements_vers_table_noeuds_et_ways():
    table = enseignes.elements_vers_table(
        [
            {"type": "node", "id": 1, "lat": 48.0, "lon": 2.0, "tags": {"brand": "Super U"}},
            {"type": "way", "id": 2, "center": {"lat": 45.0, "lon": 5.0}, "tags": {"name": "Esso"}},
            {"type": "relation", "id": 3, "tags": {}},
        ]
    )
    assert table["osm_id"].to_list() == ["node/1", "way/2"]
    assert table["enseigne"].to_list() == ["Système U", "Esso"]


def test_apparier_respecte_le_rayon():
    stations = pl.DataFrame({"id": [10, 20, 30], "latitude": [48.0, 45.0, None], "longitude": [2.0, 5.0, 3.0]})
    osm = enseignes.elements_vers_table(
        [
            {"type": "node", "id": 1, "lat": 48.0005, "lon": 2.0, "tags": {"brand": "E.Leclerc"}},  # ~55 m
            {"type": "node", "id": 2, "lat": 45.01, "lon": 5.0, "tags": {"brand": "Shell"}},  # ~1,1 km
        ]
    )
    resultat = enseignes.apparier(stations, osm)
    assert resultat["station_id"].to_list() == [10, 20]
    leclerc, loin = resultat.rows(named=True)
    assert leclerc["enseigne"] == "E.Leclerc"
    assert leclerc["grande_surface"] is True
    assert leclerc["distance_m"] == pytest.approx(55.6, abs=1)
    assert loin["enseigne"] is None
    assert loin["osm_id"] is None
    assert loin["grande_surface"] is False
