"""Le projet en production : contrôles de qualité des données collectées et suivi de la prévision hebdomadaire.

Chaque matin, `controler` vérifie la collecte du jour (nombre de stations, fraîcheur des prix, prix médian plausible,
bornes, Brent, enseignes) et dit si quelque chose cloche. Chaque semaine, `prevoir` prévoit le prix médian du gazole
de la semaine suivante avec le modèle retenu au notebook 05 (régression ridge sur les variations du Brent) et tient
un journal des prévisions, que la semaine suivante vient confronter à la réalité.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from carburants import analyse, transmission
from carburants.web import FRAICHEUR_JOURS, dernier

ORDRE = {"ok": 0, "alerte": 1, "erreur": 2}
CARBURANT = "Gazole"
VARIABLES = [f"cout_{k}" for k in range(4)]  # variations du Brent sur les quatre dernières semaines
# FRED publie le Brent avec environ dix jours de retard : le lundi matin où la prévision est faite, le dernier cours
# connu date d'environ neuf jours avant la fin de la semaine écoulée. Le modèle n'utilise que ce qui était publié.
RETARD_BRENT_JOURS = 9
# Le modèle dérive si, sur au moins 8 semaines prévues en direct, il se trompe de 20 % de plus que la persistance
SEMAINES_DERIVE, MINIMUM_DERIVE, SEUIL_DERIVE = 12, 8, 1.2


# --- Contrôles de qualité -------------------------------------------------------------------------------------------


def _controle(nom: str, valeur, attendu: str, statut: str) -> dict:
    return {"nom": nom, "valeur": valeur, "attendu": attendu, "statut": statut}


def _seuil(valeur: float, erreur: float, alerte: float) -> str:
    """« erreur » sous le seuil d'erreur, « alerte » sous le seuil d'alerte, « ok » sinon."""
    return "erreur" if valeur < erreur else "alerte" if valeur < alerte else "ok"


def _age(fichier: Path | None, prefixe: str, jour: date) -> int | None:
    if fichier is None:
        return None
    return (jour - date.fromisoformat(fichier.stem.removeprefix(prefixe)[:10])).days


def controler(dossier: Path, jour: date) -> dict:
    """Les contrôles de la collecte du `jour` sur le dossier de données (branche data), et le statut le plus grave."""
    controles = []
    photo = dernier(dossier / "prix" / "instantane", "date=*")
    jour_photo = date.fromisoformat(photo.name.removeprefix("date=")) if photo else None
    controles.append(_controle("Date de la photo des prix", jour_photo.isoformat() if jour_photo else None,
                               "celle du jour", "ok" if jour_photo == jour else "erreur"))
    if photo is not None and not all((photo / f).exists() for f in ("stations.parquet", "prix.parquet")):
        controles.append(_controle("Fichiers de la photo", "incomplets", "stations et prix", "erreur"))
        photo = None
    if photo is not None:
        stations = pl.read_parquet(photo / "stations.parquet")
        prix = pl.read_parquet(photo / "prix.parquet")
        controles.append(_controle("Stations dans la photo", stations.height, "au moins 9 000",
                                   _seuil(stations.height, 8000, 9000)))
        recents = prix.filter(pl.col("carburant") == CARBURANT,
                              pl.col("maj").dt.date() > jour_photo - timedelta(days=FRAICHEUR_JOURS))
        part = recents["station_id"].n_unique() / max(stations.height, 1) * 100
        controles.append(_controle("Stations avec un prix du gazole récent (%)", round(part, 1), "au moins 65 %",
                                   _seuil(part, 40, 65)))
        mediane = recents["prix"].median()
        plausible = mediane is not None and 1.0 <= mediane <= 3.5
        controles.append(_controle("Prix médian du gazole (€/L)", round(mediane, 3) if mediane else None,
                                   "entre 1,00 et 3,50 €", "ok" if plausible else "erreur"))
        precedente = sorted((dossier / "prix" / "instantane").glob("date=*"))[:-1]
        if precedente and mediane is not None:
            avant = pl.read_parquet(precedente[-1] / "prix.parquet").filter(
                pl.col("carburant") == CARBURANT,
                pl.col("maj").dt.date() > date.fromisoformat(precedente[-1].name.removeprefix("date="))
                - timedelta(days=FRAICHEUR_JOURS),
            )["prix"].median()
            if avant:
                variation = (mediane / avant - 1) * 100
                controles.append(_controle("Variation du prix médian depuis la photo précédente (%)", round(variation, 2),
                                           "moins de 5 % en valeur absolue", "alerte" if abs(variation) >= 5 else "ok"))

    irve = dernier(dossier / "irve", "bornes_*.parquet")
    age = _age(irve, "bornes_", jour)
    controles.append(_controle("Âge du fichier des bornes (jours)", age, "au plus 14 jours",
                               "erreur" if age is None else "alerte" if age > 14 else "ok"))
    if irve is not None:
        lignes = pl.scan_parquet(irve).select(pl.len()).collect().item()
        controles.append(_controle("Lignes du fichier des bornes", lignes, "au moins 100 000",
                                   _seuil(lignes, 100_000, 100_000)))

    marches = dossier / "marches" / "brent_change.parquet"
    if marches.exists():
        derniere = pl.read_parquet(marches)["date"].max()
        retard = (jour - derniere).days
        controles.append(_controle("Retard du cours du Brent (jours)", retard, "au plus 14 jours (FRED publie avec retard)",
                                   "alerte" if retard > 14 else "ok"))
    else:
        controles.append(_controle("Retard du cours du Brent (jours)", None, "au plus 14 jours", "alerte"))

    fichier_enseignes = dernier(dossier / "enseignes", "stations_enseignes_*.parquet")
    if fichier_enseignes is None:
        controles.append(_controle("Stations avec une enseigne (%)", None, "au moins 70 %", "alerte"))
    else:
        couverture = pl.read_parquet(fichier_enseignes)["enseigne"].is_not_null().mean() * 100
        controles.append(_controle("Stations avec une enseigne (%)", round(couverture, 1), "au moins 70 %",
                                   _seuil(couverture, 50, 70)))
    statut = max((c["statut"] for c in controles), key=ORDRE.__getitem__)
    return {"date": jour.isoformat(), "statut": statut, "controles": controles}


def resume_markdown(rapport: dict) -> str:
    """Le rapport en tableau Markdown, pour le résumé du workflow."""
    icones = {"ok": "✅", "alerte": "⚠️", "erreur": "❌"}
    lignes = [f"### Contrôles de la collecte du {rapport['date']} : {icones[rapport['statut']]} {rapport['statut']}", "",
              "| Contrôle | Valeur | Attendu | |", "|---|---|---|---|"]
    lignes += [f"| {c['nom']} | {c['valeur']} | {c['attendu']} | {icones[c['statut']]} |" for c in rapport["controles"]]
    return "\n".join(lignes) + "\n"


# --- Prévision hebdomadaire -----------------------------------------------------------------------------------------


def serie_nationale(historique: Path, annees: list[int]) -> pl.DataFrame:
    """Le prix médian national du gazole chaque semaine (datée de son lundi) des `annees`, comme au notebook 05.

    Une semaine appartient à l'année de son dimanche. Chaque année est calculée avec la précédente, pour que les
    prix publiés en décembre et prolongés en janvier comptent. Une semaine n'est gardée que si les prix vont jusqu'à
    son samedi, dernier jour où presque toutes les stations mettent à jour leurs prix : l'archive téléchargée le
    lundi matin s'arrête au samedi ou au dimanche selon l'heure de sa mise à jour.
    """
    morceaux = []
    disponibles = {int(f.stem.removeprefix("prix_")) for f in historique.glob("prix_*.parquet")}
    for annee in annees:
        prix = analyse.charger_historique(historique, [a for a in (annee - 1, annee) if a in disponibles])
        prix = prix.filter(pl.col("carburant") == CARBURANT)
        fin = prix.select(pl.col("maj").max().dt.date()).collect().item()
        dimanche = pl.col("semaine") + pl.duration(days=6)
        samedi = pl.col("semaine") + pl.duration(days=5)
        hebdo = analyse.prix_hebdo(prix).filter(dimanche.dt.year() == annee, samedi <= fin)
        morceaux.append(hebdo.group_by("semaine").agg(prix=pl.col("prix").median(), stations=pl.len()))
    return pl.concat(morceaux).sort("semaine")


def fusionner_serie(ancienne: pl.DataFrame | None, nouvelle: pl.DataFrame) -> pl.DataFrame:
    """La série stockée, dont les semaines recalculées remplacent les anciennes valeurs."""
    if ancienne is None:
        return nouvelle.sort("semaine")
    return pl.concat([ancienne.filter(~pl.col("semaine").is_in(nouvelle["semaine"].implode())), nouvelle]).sort("semaine")


def table(serie: pl.DataFrame, marches: pl.DataFrame, retard_jours: int = RETARD_BRENT_JOURS):
    """Les variables de prévision (`modele.table_prevision`). Le Brent d'une semaine est celui qui était publié le
    lundi suivant : la moyenne de sept jours qui finissent `retard_jours` avant le dimanche (délai de FRED). Le
    modèle est estimé et utilisé avec ce même décalage ; s'il manque un cours, le dernier connu le remplace."""
    from carburants import modele

    publie = marches.with_columns(pl.col("date") + pl.duration(days=retard_jours))
    brent = transmission.brent_hebdo(publie).select(
        (pl.col("date") - pl.duration(weeks=1)).alias("semaine"), pl.col("brent_eur_litre").alias("cout")
    )
    jointe = serie.select("semaine", "prix").join(brent, on="semaine", how="left").sort("semaine")
    jointe = jointe.with_columns(pl.col("cout").forward_fill()).drop_nulls("cout")
    return modele.table_prevision(jointe)


def _ridge():
    from sklearn.linear_model import RidgeCV

    return RidgeCV(alphas=np.logspace(-6, 0, 13))  # comme au notebook 05


def prevoir_semaine(t, i: int) -> dict:
    """La prévision faite à la fin de la semaine de la ligne `i` pour la semaine suivante, avec un modèle estimé
    sur les seules semaines dont la variation suivante était connue à ce moment-là."""
    from carburants.modele import remise_fin_de_semaine  # pandas, inutile aux contrôles quotidiens et au site

    passe = t.loc[: i - 1].dropna(subset=[*VARIABLES, "cible"])
    modele = _ridge().fit(passe[VARIABLES], passe["cible"])
    variation = float(modele.predict(t.loc[[i], VARIABLES])[0])
    semaine = t.loc[i, "semaine"]
    suivante = semaine + timedelta(weeks=1)
    # Le modèle prévoit le prix hors remise (prix affiché + remise) : une remise qui augmente fait baisser le prix affiché
    variation_remise = remise_fin_de_semaine(suivante) - remise_fin_de_semaine(semaine)
    base = float(t.loc[i, "prix_affiche"])
    return {
        "semaine": suivante,
        "prix_base": round(base, 4),
        "prevision": round(base + variation - variation_remise, 4),
        "persistance": round(base - variation_remise, 4),
    }


def prevoir(serie: pl.DataFrame, marches: pl.DataFrame, journal: pl.DataFrame | None, aujourd_hui: date,
            retro_depuis: date | None = None) -> pl.DataFrame:
    """Le journal des prévisions, complété par la prévision de la semaine qui suit la dernière semaine connue.

    Au premier lancement (journal vide), les semaines depuis `retro_depuis` sont prévues « après coup », chacune avec
    un modèle estimé sur le seul passé et le Brent publié à l'époque, pour que le suivi ne parte pas de zéro ; elles
    sont marquées comme rétrospectives. La prévision en direct n'est faite que pour une semaine qui n'est pas encore
    finie le jour du lancement (sinon l'historique est en retard et il n'y a rien à prévoir).
    """
    t = table(serie, marches)
    utilisable = t.dropna(subset=VARIABLES).reset_index(drop=True)
    lignes = []
    if (journal is None or journal.is_empty()) and retro_depuis is not None:
        for i in utilisable.index[utilisable["semaine"] >= retro_depuis][:-1]:
            lignes.append(prevoir_semaine(utilisable, i) | {"faite_le": None, "mode": "rétrospectif"})
    directe = prevoir_semaine(utilisable, utilisable.index[-1])
    if directe["semaine"] + timedelta(days=6) >= aujourd_hui:
        lignes.append(directe | {"faite_le": aujourd_hui, "mode": "direct"})
    nouvelles = pl.DataFrame(lignes, schema={
        "semaine": pl.Date, "prix_base": pl.Float64, "prevision": pl.Float64, "persistance": pl.Float64,
        "faite_le": pl.Date, "mode": pl.Utf8,
    })
    if journal is None or journal.is_empty():
        return nouvelles.sort("semaine")
    # Une prévision « en direct » déjà au journal n'est jamais réécrite : elle est jugée telle qu'elle a été faite
    nouvelles = nouvelles.filter(~pl.col("semaine").is_in(journal["semaine"].implode()))
    return pl.concat([journal, nouvelles]).sort("semaine")


def evaluer(journal: pl.DataFrame, serie: pl.DataFrame) -> pl.DataFrame:
    """Le journal avec le prix réel de chaque semaine prévue (null tant qu'elle n'est pas finie) et les erreurs."""
    return (
        journal.join(serie.select("semaine", pl.col("prix").alias("reel")), on="semaine", how="left")
        .with_columns(
            erreur=(pl.col("prevision") - pl.col("reel")).abs() * 100,
            erreur_persistance=(pl.col("persistance") - pl.col("reel")).abs() * 100,
        )
        .sort("semaine")
    )


def bilan(evalue: pl.DataFrame, semaines: int = SEMAINES_DERIVE) -> dict:
    """Les erreurs moyennes (centimes) des `semaines` dernières prévisions jugées, et une alerte si le modèle dérive :
    sur les dernières prévisions faites en direct (au moins `MINIMUM_DERIVE`), il se trompe nettement plus que la
    persistance. Comparer à la persistance, jugée sur les mêmes semaines, ne confond pas un marché agité avec un
    modèle usé."""
    jugees = evalue.drop_nulls("reel")
    recentes = jugees.tail(semaines)
    directes = jugees.filter(pl.col("mode") == "direct").tail(semaines)

    def moyenne(table, colonne):
        return round(table[colonne].mean(), 2) if table.height else None

    erreur_directe, persistance_directe = moyenne(directes, "erreur"), moyenne(directes, "erreur_persistance")
    return {
        "semaines_jugees": jugees.height,
        "semaines_en_direct": jugees.filter(pl.col("mode") == "direct").height,
        "erreur_recente": moyenne(recentes, "erreur"),
        "erreur_persistance_recente": moyenne(recentes, "erreur_persistance"),
        "erreur_directe": erreur_directe,
        "erreur_persistance_directe": persistance_directe,
        "derive": directes.height >= MINIMUM_DERIVE and erreur_directe > SEUIL_DERIVE * persistance_directe,
    }


def ecrire_json(objet, chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(objet, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
