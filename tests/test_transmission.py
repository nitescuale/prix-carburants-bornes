from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from carburants import transmission

pytest.importorskip("statsmodels")


def _simuler(semaines=1500, hausse=0.8, baisse=0.3, rappel=-0.3, graine=1):
    """Un prix qui suit le coût avec une marge fixe, plus vite à la hausse qu'à la baisse."""
    rng = np.random.default_rng(graine)
    cout = 0.4 + np.cumsum(rng.normal(0, 0.01, semaines))
    prix = np.empty(semaines)
    prix[0] = cout[0] + 0.2
    for t in range(1, semaines):
        dc = cout[t] - cout[t - 1]
        prix[t] = prix[t - 1] + hausse * max(dc, 0) + baisse * min(dc, 0) + rappel * (prix[t - 1] - cout[t - 1] - 0.2)
        prix[t] += rng.normal(0, 0.002)
    dates = [date(2000, 1, 3) + timedelta(weeks=t) for t in range(semaines)]
    return pl.DataFrame({"date": dates, "prix": prix, "cout": cout})


def test_estimer_retrouve_l_asymetrie_simulee():
    modele = transmission.estimer(_simuler(), retards_cout=2, retards_prix=1, tendance_df=0)
    assert modele.phi == pytest.approx(1, abs=0.05)
    assert modele.coef("hausse_0") == pytest.approx(0.8, abs=0.05)
    assert modele.coef("baisse_0") == pytest.approx(0.3, abs=0.05)
    assert modele.coef("rappel") == pytest.approx(-0.3, abs=0.05)
    assert modele.test_wald([0], cumule=True)[1] < 0.001
    # À long terme, hausse et baisse se transmettent entièrement
    resultat = modele.asymetrie(horizon=30, tirages=200)
    assert resultat["hausse"][-1] == pytest.approx(modele.phi, abs=0.02)
    assert resultat["baisse"][-1] == pytest.approx(modele.phi, abs=0.02)
    assert resultat["ecart_bas"][0] > 0.3


def test_estimer_sans_asymetrie_et_semaines_manquantes():
    serie = _simuler(hausse=0.5, baisse=0.5, graine=2)
    serie = serie.filter(pl.int_range(pl.len()) % 50 != 7)  # une semaine manquante de temps en temps
    modele = transmission.estimer(serie, retards_cout=2, retards_prix=1)
    assert modele.test_wald()[1] > 0.01
    assert modele.observations < serie.height


def test_reponse_d_un_modele_connu():
    noms = ["constante", "hausse_0", "baisse_0", "prix_1", "rappel"]
    modele = transmission.Transmission(noms, np.array([0, 0.5, 0.2, 0, -0.5]), np.eye(5), 1.0, 0, 1, 0, 0)
    # t = 0 : 0,5 ; t = 1 : 0,5 - 0,5 × (0,5 - 1) = 0,75 ; t = 2 : 0,875
    assert modele.reponse(1, 2) == pytest.approx([0.5, 0.75, 0.875])
    assert modele.reponse(-1, 1) == pytest.approx([-0.2, -0.6])


def test_brent_hebdo_affecte_la_semaine_au_lundi_suivant():
    marches = pl.DataFrame(
        {
            "date": [date(2026, 10, 5), date(2026, 10, 9), date(2026, 10, 12)],  # lundi, vendredi, lundi
            "brent_usd_baril": [100.0, 110.0, 120.0],
            "brent_eur_litre": [0.5, 0.6, 0.7],
        }
    )
    hebdo = transmission.brent_hebdo(marches)
    assert hebdo["date"].to_list() == [date(2026, 10, 12), date(2026, 10, 19)]
    assert hebdo["brent_eur_litre"].to_list() == pytest.approx([0.55, 0.7])


def test_remise_etat():
    jours = pl.DataFrame({"date": [date(2022, 3, 28), date(2022, 4, 4), date(2022, 9, 12), date(2022, 12, 26), date(2023, 1, 2)]})
    remises = jours.select(transmission.remise_etat()).to_series().to_list()
    assert remises == pytest.approx([0, 0.15, 0.25, 0.1 / 1.2, 0])
