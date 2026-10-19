from datetime import date

import pytest

from carburants import territoires


def test_communes_vers_table():
    elements = [
        {
            "code": "75056", "nom": "Paris", "population": 2_100_000, "surface": 10_540.0,
            "centre": {"type": "Point", "coordinates": [2.347, 48.859]}, "codeDepartement": "75", "codeRegion": "11",
        },
        {"code": "55139", "nom": "Cumières-le-Mort-Homme", "surface": 1_000.0, "codeDepartement": "55", "codeRegion": "44"},
    ]
    table = territoires.communes_vers_table(elements, "commune-actuelle")
    assert table["code_insee"].to_list() == ["75056", "55139"]
    assert table["surface_km2"].to_list() == pytest.approx([105.4, 10.0])
    assert table["population"].to_list() == [2_100_000, None]
    assert table["latitude"].to_list() == [48.859, None]


def test_choisir_ressources_et_nom_fichier():
    jeu = {
        "resources": [
            {"type": "main", "format": "csv", "title": "Voitures par commune (2025)", "url": "https://x/a.csv", "id": "1"},
            {"type": "documentation", "format": "pdf", "title": "Notice", "url": "https://x/n.pdf", "id": "2"},
            {"type": "main", "format": "xlsx", "title": "donnees.xlsx", "url": "https://x/b.xlsx", "id": "3"},
            {"type": "main", "format": "csv", "title": "Énorme", "url": "https://x/c.csv", "id": "4", "filesize": 10**9},
        ]
    }
    ressources = territoires.choisir_ressources(jeu)
    assert [r["id"] for r in ressources] == ["1", "3"]
    assert [territoires.nom_fichier(r) for r in ressources] == ["Voitures_par_commune_2025.csv", "donnees.xlsx"]


def test_eurostat_vers_table():
    donnees = {
        "id": ["freq", "geo", "time"],
        "size": [1, 1, 3],
        "dimension": {
            "freq": {"category": {"index": {"S": 0}}},
            "geo": {"category": {"index": {"FR": 0}}},
            "time": {"category": {"index": {"2024-S1": 0, "2024-S2": 1, "2025-S1": 2}}},
        },
        "value": {"0": 0.2516, "2": 0.2796},
    }
    table = territoires.eurostat_vers_table(donnees)
    assert table["semestre"].to_list() == ["2024-S1", "2025-S1"]
    assert table["debut"].to_list() == [date(2024, 1, 1), date(2025, 1, 1)]
    assert table["prix_eur_kwh"].to_list() == [0.2516, 0.2796]

    donnees["dimension"]["geo"]["category"]["index"] = {"FR": 0, "DE": 1}
    with pytest.raises(ValueError):
        territoires.eurostat_vers_table(donnees)


def test_departements_et_vehicules(tmp_path):
    assert len(territoires.DEPARTEMENTS) == 96
    chemin = tmp_path / "v.csv"
    chemin.write_bytes(
        "CODGEO,LIBGEO,EPCI,LIBEPCI,DATE_ARRETE,NB_VP_RECHARGEABLES_EL,NB_VP_RECHARGEABLES_GAZ,NB_VP\n"
        "1001,L'ABERGEMENT-CLÉMENCIAT,200069193,CC,2026-08-31,12,0,900\n".encode("latin1")
    )
    v = territoires.lire_vehicules(chemin)
    assert v.row(0) == ("01001", date(2026, 8, 31), 12, 0, 900)
