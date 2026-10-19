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
# Erreur moyenne de la prévision (Brent seul) depuis 2022 dans le notebook 05, en centimes : la référence du suivi
ERREUR_REFERENCE = 2.74


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
    if fichier_enseignes is not None:
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
    prix publiés en décembre et prolongés en janvier comptent ; seules les semaines complètes (finies avant le
    dernier prix publié) sont gardées.
    """
    morceaux = []
    disponibles = {int(f.stem.removeprefix("prix_")) for f in historique.glob("prix_*.parquet")}
    for annee in annees:
        prix = analyse.charger_historique(historique, [a for a in (annee - 1, annee) if a in disponibles])
        prix = prix.filter(pl.col("carburant") == CARBURANT)
        fin = prix.select(pl.col("maj").max().dt.date()).collect().item()
        dimanche = pl.col("semaine") + pl.duration(days=6)
        hebdo = analyse.prix_hebdo(prix).filter(dimanche.dt.year() == annee, dimanche <= fin)
        morceaux.append(hebdo.group_by("semaine").agg(prix=pl.col("prix").median(), stations=pl.len()))
    return pl.concat(morceaux).sort("semaine")


def fusionner_serie(ancienne: pl.DataFrame | None, nouvelle: pl.DataFrame) -> pl.DataFrame:
    """La série stockée, dont les semaines recalculées remplacent les anciennes valeurs."""
    if ancienne is None:
        return nouvelle.sort("semaine")
    return pl.concat([ancienne.filter(~pl.col("semaine").is_in(nouvelle["semaine"].implode())), nouvelle]).sort("semaine")


def table(serie: pl.DataFrame, marches: pl.DataFrame):
    """Les variables de prévision (`modele.table_prevision`) : le Brent connu à la fin de chaque semaine est la
    moyenne des sept jours qui précèdent le lundi suivant ; s'il n'est pas encore publié, le dernier connu le remplace."""
    from carburants import modele

    brent = transmission.brent_hebdo(marches).select(
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
    passe = t.loc[: i - 1].dropna(subset=[*VARIABLES, "cible"])
    modele = _ridge().fit(passe[VARIABLES], passe["cible"])
    variation = float(modele.predict(t.loc[[i], VARIABLES])[0])
    remise = float(t.loc[i, "variation_remise"]) if not np.isnan(t.loc[i, "variation_remise"]) else 0.0
    base = float(t.loc[i, "prix_affiche"])
    return {
        "semaine": t.loc[i, "semaine"] + timedelta(weeks=1),
        "prix_base": round(base, 4),
        "prevision": round(base + variation + remise, 4),
        "persistance": round(base + remise, 4),
    }


def prevoir(serie: pl.DataFrame, marches: pl.DataFrame, journal: pl.DataFrame | None, aujourd_hui: date,
            retro_depuis: date | None = None) -> pl.DataFrame:
    """Le journal des prévisions, complété par la prévision de la semaine qui suit la dernière semaine connue.

    Au premier lancement (journal vide), les semaines depuis `retro_depuis` sont prévues « après coup », chacune avec
    les seules données de l'époque, pour que le suivi ne parte pas de zéro ; elles sont marquées comme rétrospectives.
    """
    t = table(serie, marches)
    utilisable = t.dropna(subset=VARIABLES).reset_index(drop=True)
    lignes = []
    if (journal is None or journal.is_empty()) and retro_depuis is not None:
        for i in utilisable.index[utilisable["semaine"] >= retro_depuis][:-1]:
            lignes.append(prevoir_semaine(utilisable, i) | {"faite_le": None, "mode": "rétrospectif"})
    lignes.append(prevoir_semaine(utilisable, utilisable.index[-1]) | {"faite_le": aujourd_hui, "mode": "direct"})
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


def bilan(evalue: pl.DataFrame, semaines: int = 12) -> dict:
    """Les erreurs moyennes (centimes) des dernières prévisions jugées, et une alerte si le modèle dérive : erreur
    sur les `semaines` dernières plus de deux fois supérieure à celle du notebook 05 depuis 2022."""
    jugees = evalue.drop_nulls("reel")
    recentes = jugees.tail(semaines)
    erreur = recentes["erreur"].mean() if recentes.height else None
    return {
        "semaines_jugees": jugees.height,
        "semaines_en_direct": jugees.filter(pl.col("mode") == "direct").height,
        "erreur_recente": round(erreur, 2) if erreur is not None else None,
        "erreur_persistance_recente": round(recentes["erreur_persistance"].mean(), 2) if recentes.height else None,
        "erreur_reference": ERREUR_REFERENCE,
        "derive": erreur is not None and recentes.height >= 4 and erreur > 2 * ERREUR_REFERENCE,
    }


def ecrire_json(objet, chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(objet, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
