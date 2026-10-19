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
        (None, "Garage du Carrefour", None),
        (None, "Relais du Casino", None),
        (None, "Géant des Pneus", None),
        ("Élan", None, "TotalEnergies"),
        ("Système U", None, "Système U"),
        (None, "Intermarché Saint-Jean", "Intermarché"),
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
    assert leclerc["osm_partage"] is False


def test_apparier_signale_une_station_osm_partagee():
    stations = pl.DataFrame({"id": [1, 2], "latitude": [48.0, 48.0001], "longitude": [2.0, 2.0]})
    osm = enseignes.elements_vers_table([{"type": "node", "id": 9, "lat": 48.0, "lon": 2.0, "tags": {"brand": "BP"}}])
    resultat = enseignes.apparier(stations, osm)
    assert resultat["osm_partage"].to_list() == [True, True]


def test_apparier_refuse_une_table_osm_vide():
    stations = pl.DataFrame({"id": [1], "latitude": [48.0], "longitude": [2.0]})
    with pytest.raises(ValueError, match="Aucune station"):
        enseignes.apparier(stations, enseignes.elements_vers_table([]))


def test_requete_overpass_rejette_une_reponse_partielle(monkeypatch):
    reponses = [
        {"remark": "runtime error: Query timed out in \"query\" at line 3 after 300 seconds.", "elements": [{"id": 1}]},
        {"elements": [{"type": "node", "id": 1}]},
    ]

    class Reponse:
        def __init__(self, contenu):
            self.contenu = contenu

        def raise_for_status(self):
            pass

        def json(self):
            return self.contenu

    monkeypatch.setattr(enseignes.requests, "post", lambda url, data, headers, timeout: Reponse(reponses.pop(0)))
    monkeypatch.setattr(enseignes.time, "sleep", lambda s: None)
    assert enseignes._requete_overpass("[out:json];") == [{"type": "node", "id": 1}]
    assert reponses == []


def test_telecharger_osm_parcourt_les_tuiles_et_dedoublonne(monkeypatch):
    appels = []

    def faux_overpass(requete):
        appels.append(requete)
        # La même station revient dans chaque tuile
        return [{"type": "node", "id": 1, "lat": 44.5, "lon": 2.5, "tags": {"brand": "Avia"}}]

    monkeypatch.setattr(enseignes, "_requete_overpass", faux_overpass)
    monkeypatch.setattr(enseignes.time, "sleep", lambda s: None)
    table = enseignes.telecharger_osm()
    assert len(appels) == len(enseignes.TUILES)
    assert "(41.0,-5.5,44.5,2.5)" in appels[0]
    assert table["osm_id"].to_list() == ["node/1"]


def test_requete_overpass_reessaie_puis_echoue(monkeypatch):
    tentatives = []

    def post(url, data, headers, timeout):
        tentatives.append(url)
        raise enseignes.requests.ConnectionError("saturé")

    monkeypatch.setattr(enseignes.requests, "post", post)
    monkeypatch.setattr(enseignes.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="saturé"):
        enseignes._requete_overpass("[out:json];")
    assert len(tentatives) == 6
    assert set(tentatives) == set(enseignes.OVERPASS)
