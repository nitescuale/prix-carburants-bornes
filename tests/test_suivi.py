from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from carburants import suivi

JOUR = date(2026, 10, 19)


def _photo(dossier, jour, stations=9500, prix=2.30, recents=0.8):
    photo = dossier / "prix" / "instantane" / f"date={jour.isoformat()}"
    photo.mkdir(parents=True)
    pl.DataFrame({"id": list(range(stations))}).write_parquet(photo / "stations.parquet")
    n_recents = int(stations * recents)
    pl.DataFrame({
        "station_id": list(range(stations)),
        "carburant": ["Gazole"] * stations,
        "maj": [datetime.combine(jour, datetime.min.time()) - timedelta(days=0 if i < n_recents else 30) for i in range(stations)],
        "prix": [prix] * stations,
    }).write_parquet(photo / "prix.parquet")


def _annexes(dossier, jour):
    (dossier / "irve").mkdir(parents=True)
    pl.DataFrame({"x": list(range(120_000))}).write_parquet(dossier / "irve" / f"bornes_{jour.isoformat()}.parquet")
    (dossier / "marches").mkdir(parents=True)
    pl.DataFrame({"date": [jour - timedelta(days=9)], "brent_eur_litre": [0.5]}).write_parquet(
        dossier / "marches" / "brent_change.parquet")
    (dossier / "enseignes").mkdir(parents=True)
    pl.DataFrame({"enseigne": ["Total"] * 8 + [None] * 2}).write_parquet(
        dossier / "enseignes" / f"stations_enseignes_{jour.isoformat()}.parquet")


def test_controler_tout_va_bien(tmp_path):
    _photo(tmp_path, JOUR - timedelta(days=1), prix=2.25)
    _photo(tmp_path, JOUR)
    _annexes(tmp_path, JOUR)
    rapport = suivi.controler(tmp_path, JOUR)
    assert rapport["statut"] == "ok", rapport
    valeurs = {c["nom"]: c["valeur"] for c in rapport["controles"]}
    assert valeurs["Stations dans la photo"] == 9500
    assert valeurs["Stations avec un prix du gazole récent (%)"] == 80.0
    assert valeurs["Variation du prix médian depuis la photo précédente (%)"] == pytest.approx(2.22, abs=0.01)
    assert "| Stations dans la photo | 9500 |" in suivi.resume_markdown(rapport)


def test_controler_signale_les_anomalies(tmp_path):
    # Photo de la veille seulement, peu de prix récents, prix médian absurde, pas de bornes
    _photo(tmp_path, JOUR - timedelta(days=1), stations=8500, prix=9.0, recents=0.5)
    rapport = suivi.controler(tmp_path, JOUR)
    statuts = {c["nom"]: c["statut"] for c in rapport["controles"]}
    assert rapport["statut"] == "erreur"
    assert statuts["Date de la photo des prix"] == "erreur"
    assert statuts["Stations dans la photo"] == "alerte"
    assert statuts["Stations avec un prix du gazole récent (%)"] == "alerte"
    assert statuts["Prix médian du gazole (€/L)"] == "erreur"
    assert statuts["Âge du fichier des bornes (jours)"] == "erreur"


def _historique(dossier):
    """Une station qui publie un prix le 31 décembre 2024, prolongé en janvier, puis chaque semaine de 2025."""
    lignes = [(1, datetime(2024, 12, 31, 9), 1.60)]
    lignes += [(1, datetime(2025, 1, 6) + timedelta(weeks=k, days=1), 1.60 + k / 100) for k in range(10)]
    table = pl.DataFrame(lignes, schema=["station_id", "maj", "prix"], orient="row").with_columns(
        carburant=pl.lit("Gazole"), carburant_id=pl.lit(1, dtype=pl.Int64)
    )
    table.filter(pl.col("maj").dt.year() == 2024).write_parquet(dossier / "prix_2024.parquet")
    table.filter(pl.col("maj").dt.year() == 2025).write_parquet(dossier / "prix_2025.parquet")


def test_serie_nationale(tmp_path):
    _historique(tmp_path)
    serie = suivi.serie_nationale(tmp_path, [2025])
    # La semaine du lundi 30 décembre 2024 finit en 2025 : elle appartient à 2025, avec le prix du 31 décembre
    assert serie["semaine"][0] == date(2024, 12, 30) and serie["prix"][0] == 1.60
    # La dernière semaine (du 10 mars, dernier prix le mardi 11) n'est pas finie : elle n'est pas gardée ;
    # une semaine dont les prix vont jusqu'au samedi l'est (voir test_serie_semaine_jusqu_au_samedi)
    assert serie["semaine"][-1] == date(2025, 3, 3)
    assert serie["prix"][-1] == pytest.approx(1.68)
    remplacee = suivi.fusionner_serie(serie.with_columns(prix=pl.lit(0.0)), serie.tail(2))
    assert remplacee.height == serie.height and remplacee["prix"].to_list()[-2:] == serie["prix"].to_list()[-2:]
    assert remplacee["prix"][0] == 0.0


def test_serie_semaine_jusqu_au_samedi(tmp_path):
    lignes = [(1, datetime(2025, 3, 3) + timedelta(days=d, hours=8), 1.60 + d / 100) for d in range(6)]  # lundi-samedi
    pl.DataFrame(lignes, schema=["station_id", "maj", "prix"], orient="row").with_columns(
        carburant=pl.lit("Gazole"), carburant_id=pl.lit(1, dtype=pl.Int64)
    ).write_parquet(tmp_path / "prix_2025.parquet")
    serie = suivi.serie_nationale(tmp_path, [2025])
    assert serie["semaine"].to_list() == [date(2025, 3, 3)] and serie["prix"][0] == pytest.approx(1.65)


def _serie_et_brent(semaines=120, retard=3):
    """Un prix qui suit le Brent avec `retard` semaines de retard (assez pour que le Brent publié le prévoie)."""
    rng = np.random.default_rng(0)
    lundis = [date(2024, 1, 1) + timedelta(weeks=k) for k in range(semaines)]
    brent = 0.5 + np.cumsum(rng.normal(0, 0.02, semaines))
    prix = 1.7 + np.concatenate([np.zeros(retard), brent[:-retard] - 0.5]) * 1.2
    jours = [lundi + timedelta(days=d) for lundi in lundis for d in range(7)]
    marches = pl.DataFrame({"date": jours, "brent_eur_litre": np.repeat(brent, 7), "brent_usd_baril": np.repeat(brent, 7) * 200})
    return pl.DataFrame({"semaine": lundis, "prix": prix}), marches


def test_prevoir_et_evaluer():
    serie, marches = _serie_et_brent()
    journal = suivi.prevoir(serie.head(110), marches, None, date(2026, 2, 9), retro_depuis=date(2025, 6, 2))
    directe = journal.filter(pl.col("mode") == "direct")
    assert directe.height == 1 and directe["semaine"][0] == serie["semaine"][110]
    assert journal.filter(pl.col("mode") == "rétrospectif")["semaine"].min() == date(2025, 6, 9)
    # Le prix suit le Brent : la prévision bat la persistance
    evalue = suivi.evaluer(journal, serie)
    assert evalue["erreur"].mean() < evalue["erreur_persistance"].mean()
    # Une semaine plus tard, la prévision déjà faite n'est pas réécrite et une nouvelle s'ajoute
    suite = suivi.prevoir(serie.head(111), marches, journal.with_columns(prevision=pl.lit(9.0)), date(2026, 2, 16))
    assert suite.height == journal.height + 1
    assert suite.filter(pl.col("semaine") == serie["semaine"][110])["prevision"][0] == 9.0
    bilan = suivi.bilan(suivi.evaluer(suite, serie))
    # Deux prévisions en direct jugées, dont une fausse à 9 € : trop peu pour conclure à une dérive
    assert bilan["semaines_en_direct"] == 2 and not bilan["derive"]
    # Une prévision en direct n'est faite que pour une semaine qui n'est pas encore finie
    en_retard = suivi.prevoir(serie.head(111), marches, journal, date(2026, 3, 20))
    assert en_retard.height == journal.height


def test_bilan_juge_la_derive_contre_la_persistance():
    semaines = [date(2026, 1, 5) + timedelta(weeks=k) for k in range(10)]

    def evalue(erreur, mode="direct"):
        return pl.DataFrame({"semaine": semaines, "mode": [mode] * 10, "reel": [1.8] * 10,
                             "erreur": [erreur] * 10, "erreur_persistance": [5.0] * 10})

    # Un marché agité (grosses erreurs pour tous) n'est pas une dérive ; faire nettement pire que la persistance, si
    assert not suivi.bilan(evalue(5.5))["derive"]
    assert suivi.bilan(evalue(6.5))["derive"]
    # Les prévisions rétrospectives ne comptent pas
    assert not suivi.bilan(evalue(6.5, mode="rétrospectif"))["derive"]


def test_brent_publie_avec_retard():
    serie, marches = _serie_et_brent()
    t = suivi.table(serie, marches)
    semaine = serie["semaine"][50]
    # Le Brent connu à la fin d'une semaine est celui des sept jours qui finissent neuf jours avant son dimanche
    fin = semaine + timedelta(days=6 - suivi.RETARD_BRENT_JOURS)
    attendu = marches.filter(pl.col("date").is_between(fin - timedelta(days=6), fin))["brent_eur_litre"].mean()
    assert t.loc[t["semaine"] == semaine, "cout"].item() == pytest.approx(attendu)


def test_prevision_a_la_fin_de_la_remise():
    # Prix hors remise constant à 1,80 € : affiché 1,70 € fin 2022 (remise de 10 c), puis 1,80 € en 2023
    from carburants.modele import remise_fin_de_semaine

    lundis = [date(2022, 1, 3) + timedelta(weeks=k) for k in range(53)]
    serie = pl.DataFrame({"semaine": lundis, "prix": [1.80 - remise_fin_de_semaine(s) for s in lundis]})
    jours = [lundis[0] - timedelta(days=60) + timedelta(days=d) for d in range(500)]
    marches = pl.DataFrame({"date": jours, "brent_eur_litre": [0.5 + 0.01 * np.sin(d / 9) for d in range(500)],
                            "brent_usd_baril": [100.0] * 500})
    t = suivi.table(serie, marches).dropna(subset=suivi.VARIABLES).reset_index(drop=True)
    derniere = t.index[t["semaine"] == date(2022, 12, 26)][0]
    prevision = suivi.prevoir_semaine(t, derniere)
    assert prevision["semaine"] == date(2023, 1, 2) and prevision["prix_base"] == pytest.approx(1.70)
    assert prevision["persistance"] == pytest.approx(1.80)
    assert prevision["prevision"] == pytest.approx(1.80, abs=0.005)


def test_tableau_de_bord(tmp_path):
    from carburants import web

    assert web.tableau_de_bord(tmp_path) is None
    serie, marches = _serie_et_brent()
    journal = suivi.prevoir(serie.head(110), marches, None, date(2026, 2, 9), retro_depuis=date(2025, 6, 2))
    (tmp_path / "suivi").mkdir()
    serie.write_parquet(tmp_path / "suivi" / "serie_gazole.parquet")
    journal.write_parquet(tmp_path / "suivi" / "previsions.parquet")
    _photo(tmp_path, JOUR)
    _annexes(tmp_path, JOUR)
    rapport = suivi.controler(tmp_path, JOUR)
    suivi.ecrire_json(rapport, tmp_path / "qualite" / f"{JOUR.isoformat()}.json")

    tableau = web.tableau_de_bord(tmp_path, semaines=52)
    assert len(tableau["serie"]["lignes"]) == 52
    assert tableau["previsions"]["colonnes"][:3] == ["semaine", "prix_base", "prevision"]
    assert tableau["bilan"]["semaines_jugees"] == journal.height
    assert tableau["qualite"] == [{"date": "2026-10-19", "statut": "ok", "stations": 9500, "part_recente": 80.0,
                                   "mediane": 2.3}]
    assert tableau["dernier_controle"]["statut"] == "ok"
