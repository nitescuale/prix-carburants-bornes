from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from carburants import concurrence

# Un degré de latitude fait 111,2 km (sur une sphère de 6 371 km de rayon)
KM = 1 / 111.195


def _stations(positions_km, ids=None):
    """Des stations placées sur une ligne nord-sud, à `positions_km` du point de départ."""
    ids = ids or list(range(1, len(positions_km) + 1))
    return pl.DataFrame({"station_id": ids, "latitude": [46.5 + p * KM for p in positions_km], "longitude": [2.0] * len(ids)})


def test_voisins_compte_les_autres_stations():
    resultat = concurrence.voisins(_stations([0, 0.5, 2, 8]), [1, 5])
    assert resultat["voisins_1km"].to_list() == [1, 1, 0, 0]
    assert resultat["voisins_5km"].to_list() == [2, 2, 2, 0]
    assert resultat["plus_proche_km"].to_list() == pytest.approx([0.5, 0.5, 1.5, 6], abs=0.01)


def test_distance_plus_proche_exclut_la_station_elle_meme():
    stations = _stations([0, 1, 4])
    cibles = stations.filter(pl.col("station_id").is_in([1, 3]))
    assert concurrence.distance_plus_proche(stations, cibles).to_list() == pytest.approx([4, 1, 4], abs=0.01)
    assert concurrence.distance_plus_proche(stations, stations.head(1)).to_list()[1:] == pytest.approx([1, 4], abs=0.01)
    assert concurrence.distance_plus_proche(stations, stations.head(1))[0] is None


def test_ouvertures_ignore_les_changements_d_identifiant():
    vies = _stations([0, 0.1, 5, 20]).with_columns(
        debut=pl.Series([date(2008, 1, 7), date(2015, 3, 2), date(2015, 6, 1), date(2008, 1, 7)]),
        fin=pl.Series([date(2015, 2, 23), date(2026, 1, 5), date(2026, 1, 5), date(2026, 1, 5)]),
    )
    # La station 2 reprend la 1 au même endroit ; seule la 3 est une ouverture
    assert concurrence.ouvertures(vies, depuis=date(2009, 1, 1))["station_id"].to_list() == [3]


def test_paires_et_profil_evenement():
    t0 = date(2015, 6, 1)
    vies = _stations([0, 2, 15, 50, 1], ids=[10, 1, 2, 3, 4]).with_columns(
        debut=pl.Series([t0, date(2008, 1, 7), date(2008, 1, 7), date(2008, 1, 7), date(2015, 5, 25)]),
        fin=pl.Series([date(2026, 1, 5)] * 5),
    )
    paires = concurrence.paires_evenement(vies, vies.filter(station_id=10), avant=4, apres=8)
    # La station 3 est trop loin, la 4 n'existait pas assez tôt
    assert sorted(paires.select("station_id", "groupe").rows()) == [(1, "traitee"), (2, "temoin")]

    semaines = [t0 + timedelta(weeks=k) for k in range(-4, 9)]
    # La station traitée baisse de 2 centimes après l'ouverture, la témoin ne bouge pas
    panel = pl.DataFrame(
        {
            "station_id": [1] * 13 + [2] * 13,
            "semaine": semaines * 2,
            "ecart": [0.05 if k < 0 else 0.03 for k in range(-4, 9)] + [0.01] * 13,
        }
    )
    differences = concurrence.differences_evenement(panel, paires, avant=4, apres=8, reference=0)
    profil = concurrence.profil_evenement(differences)
    assert profil["k"].to_list() == list(range(-4, 9))
    assert profil.filter(pl.col("k") < 0)["effet"].to_list() == pytest.approx([0] * 4)
    assert profil.filter(pl.col("k") >= 0)["effet"].to_list() == pytest.approx([-0.02] * 9)
    # Une seule ouverture : pas d'erreur type
    assert profil["erreur"].is_null().all()
    assert concurrence.effet_moyen(differences, 2, 8)["effet"] == pytest.approx(-0.02)


def test_ouvertures_cas_limites():
    vies = _stations([0, 0.5, 10, 10.1, 30, 30.05]).with_columns(
        debut=pl.Series([date(2008, 1, 7), date(2015, 3, 2), date(2008, 1, 7), date(2015, 3, 2), date(2008, 1, 7), date(2009, 1, 5)]),
        fin=pl.Series([date(2015, 2, 23), date(2026, 1, 5), date(2014, 6, 2), date(2026, 1, 5), date(2009, 3, 2), date(2026, 1, 5)]),
    )
    # 2 : remplace une station à 500 m (trop loin) → ouverture ; 4 : station voisine fermée 9 mois plus tôt → ouverture ;
    # 6 : une station voisine ferme 8 semaines après son arrivée → reprise, pas une ouverture
    assert concurrence.ouvertures(vies, depuis=date(2009, 1, 1))["station_id"].to_list() == [2, 4]
    # `depuis` est inclusif
    assert concurrence.ouvertures(vies, depuis=date(2015, 3, 2))["station_id"].to_list() == [2, 4]
    assert concurrence.ouvertures(vies, depuis=date(2015, 3, 3)).is_empty()


def test_paires_exclut_les_stations_parties_et_l_anneau_intermediaire():
    t0 = date(2015, 6, 1)
    vies = _stations([0, 1, 5, 15, 12], ids=[10, 1, 2, 3, 4]).with_columns(
        debut=pl.Series([t0] + [date(2008, 1, 7)] * 4),
        fin=pl.Series([date(2026, 1, 5)] * 4 + [t0 + timedelta(weeks=4)]),
    )
    paires = concurrence.paires_evenement(vies, vies.filter(station_id=10), avant=4, apres=8)
    # 2 est entre 3 et 10 km, 4 ferme avant la fin de la fenêtre
    assert sorted(paires.select("station_id", "groupe").rows()) == [(1, "traitee"), (3, "temoin")]


def _panel(t0, stations_ecarts):
    semaines = [t0 + timedelta(weeks=k) for k in range(-10, 9)]
    return pl.DataFrame(
        {
            "station_id": [s for s, _ in stations_ecarts for _ in semaines],
            "semaine": semaines * len(stations_ecarts),
            "ecart": [f(k) for _, f in stations_ecarts for k in range(-10, 9)],
        }
    )


def test_reference_par_defaut_exclut_les_semaines_juste_avant():
    t0 = date(2015, 6, 1)
    paires = pl.DataFrame(
        {"evenement": [10, 10], "t0": [t0, t0], "station_id": [1, 2], "groupe": ["traitee", "temoin"], "distance_km": [1.0, 15.0]}
    )
    # La station traitée baisse dès k = -2 : la référence (k < -4) n'en est pas affectée
    panel = _panel(t0, [(1, lambda k: 0.05 if k < -2 else 0.03), (2, lambda k: 0.01)])
    profil = concurrence.profil_evenement(concurrence.differences_evenement(panel, paires, avant=10, apres=8))
    assert profil.filter(pl.col("k") < -2)["effet"].to_list() == pytest.approx([0] * 8)
    assert profil.filter(pl.col("k") >= -2)["effet"].to_list() == pytest.approx([-0.02] * 11)


def test_differences_sans_temoin_et_erreur_type():
    t0 = date(2015, 6, 1)
    seules = pl.DataFrame({"evenement": [10], "t0": [t0], "station_id": [1], "groupe": ["traitee"], "distance_km": [1.0]})
    panel = _panel(t0, [(1, lambda k: 0.0), (2, lambda k: 0.0), (3, lambda k: 0.0), (4, lambda k: 0.0)])
    assert concurrence.differences_evenement(panel, seules, avant=10, apres=8).is_empty()

    differences = pl.DataFrame({"evenement": [1, 2, 3, 4], "k": [0] * 4, "difference": [1.0, 2.0, 3.0, 6.0]})
    effet = concurrence.effet_moyen(differences, 0, 0)
    assert effet["effet"] == pytest.approx(3.0)
    assert effet["erreur"] == pytest.approx(np.std([1, 2, 3, 6], ddof=1) / 2)
    # Regroupées par paires : écarts à la moyenne sommés par grappe (-3, +3), correction g/(g-1) = 2
    grappes = pl.DataFrame({"evenement": [1, 2, 3, 4], "grappe": ["a", "a", "b", "b"]})
    attendu = np.sqrt((9 + 9) * 2) / 4
    assert concurrence.effet_moyen(differences, 0, 0, grappes)["erreur"] == pytest.approx(attendu)
