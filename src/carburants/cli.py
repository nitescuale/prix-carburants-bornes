"""Ligne de commande : `carburants instantane`, `carburants annee 2024`, `carburants irve`, `carburants enseignes`,
`carburants marches`, `carburants bulletin`, `carburants territoires`."""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

import polars as pl

from carburants import enseignes, irve, marches, prix, territoires, web


def collecter_instantane(dossier: Path) -> None:
    stations, tarifs = prix.collecter_zip(prix.URL_INSTANTANE)
    cible = dossier / "prix" / "instantane" / f"date={date.today().isoformat()}"
    cible.mkdir(parents=True, exist_ok=True)
    stations.write_parquet(cible / "stations.parquet")
    tarifs.write_parquet(cible / "prix.parquet")
    print(f"{len(stations)} stations, {len(tarifs)} prix -> {cible}")


def collecter_annee(dossier: Path, annee: int) -> None:
    stations, tarifs = prix.collecter_zip(prix.url_annee(annee))
    cible = dossier / "prix" / "historique" / f"annee={annee}"
    cible.mkdir(parents=True, exist_ok=True)
    stations.write_parquet(cible / "stations.parquet")
    tarifs.write_parquet(cible / "prix.parquet")
    print(f"{annee} : {len(stations)} stations, {len(tarifs)} changements de prix -> {cible}")


def collecter_irve(dossier: Path) -> None:
    bornes = irve.telecharger()
    cible = dossier / "irve"
    cible.mkdir(parents=True, exist_ok=True)
    bornes.write_parquet(cible / f"bornes_{date.today().isoformat()}.parquet")
    print(f"{len(bornes)} points de recharge -> {cible}")


def collecter_enseignes(dossier: Path) -> None:
    photos = sorted((dossier / "prix" / "instantane").glob("date=*/stations.parquet"))
    if not photos:
        raise SystemExit("Aucune photo des stations : lancer d'abord `carburants instantane`")
    stations = pl.read_parquet(photos[-1])
    resultat = enseignes.apparier(stations, enseignes.telecharger_osm())
    cible = dossier / "enseignes"
    cible.mkdir(parents=True, exist_ok=True)
    resultat.write_parquet(cible / f"stations_enseignes_{date.today().isoformat()}.parquet")
    trouvees = resultat.filter(pl.col("enseigne").is_not_null()).height
    print(f"{trouvees}/{resultat.height} stations avec une enseigne ({trouvees / resultat.height:.0%}) -> {cible}")


def collecter_marches(dossier: Path) -> None:
    cours = marches.telecharger_fred()
    cible = dossier / "marches"
    cible.mkdir(parents=True, exist_ok=True)
    # Tout l'historique est retéléchargé chaque jour : un seul fichier, remplacé
    cours.write_parquet(cible / "brent_change.parquet")
    dernier = cours.row(-1, named=True)
    print(
        f"{cours.height} jours de cotation, dernier le {dernier['date']} : Brent {dernier['brent_usd_baril']:.2f} $/baril, "
        f"soit {dernier['brent_eur_litre']:.3f} €/litre -> {cible}"
    )


def collecter_bulletin(dossier: Path) -> None:
    url, contenu = marches.telecharger_bulletin()
    cible = dossier / "marches" / "bulletin_petrolier"
    cible.mkdir(parents=True, exist_ok=True)
    (cible / "historique_prix.xlsx").write_bytes(contenu)
    (cible / "source.txt").write_text(url + "\n")
    print(f"Bulletin pétrolier : {len(contenu) / 1e6:.1f} Mo depuis {url} -> {cible}")


def collecter_territoires(dossier: Path) -> None:
    cible = dossier / "territoires"
    (cible / "vehicules").mkdir(parents=True, exist_ok=True)
    communes = territoires.telecharger_communes()
    communes.write_parquet(cible / "communes.parquet")
    print(f"{communes.height} communes et arrondissements -> {cible}")
    electricite = territoires.telecharger_prix_electricite()
    electricite.write_parquet(cible / "prix_electricite.parquet")
    dernier = electricite.row(-1, named=True)
    print(f"Électricité des ménages : {electricite.height} semestres, {dernier['semestre']} à {dernier['prix_eur_kwh']:.4f} €/kWh")
    manifeste, fichiers = territoires.telecharger_vehicules()
    for nom, contenu in fichiers:
        (cible / "vehicules" / nom).write_bytes(contenu)
    territoires.ecrire_manifeste(cible / "vehicules" / "source.json", manifeste)
    print(f"Voitures par type de recharge : {len(fichiers)} fichiers ({manifeste['jeu']}) -> {cible / 'vehicules'}")


def expliquer_stations(dossier: Path, annee: int, sortie: Path) -> None:
    from carburants import explication

    resultat = explication.expliquer(explication.preparer(dossier, annee))
    explication.ecrire(resultat, annee, sortie)
    print(f"{resultat.height} stations expliquées ({annee}) -> {sortie}")


def construire_site(dossier: Path, sortie: Path) -> None:
    chiffres = web.construire(dossier, sortie)
    gazole = chiffres["carburants"].get("Gazole", {}).get("mediane")
    print(f"Site du {chiffres['date_prix']} : {chiffres['stations']} stations, {chiffres['sites_rapides']} sites de recharge "
          f"rapide, gazole médian {gazole} €/L -> {sortie}")


def controler_collecte(dossier: Path) -> None:
    from carburants import suivi

    rapport = suivi.controler(dossier, date.today())
    suivi.ecrire_json(rapport, dossier / "qualite" / f"{rapport['date']}.json")
    texte = suivi.resume_markdown(rapport)
    print(texte)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as resume:
            resume.write(texte)
    if rapport["statut"] == "erreur":
        raise SystemExit("Au moins un contrôle est en erreur")


def suivre_prevision(dossier: Path, historique: Path, complet: bool) -> None:
    from carburants import suivi

    cible = dossier / "suivi"
    fichier_serie, fichier_journal = cible / "serie_gazole.parquet", cible / "previsions.parquet"
    ancienne = None if complet or not fichier_serie.exists() else pl.read_parquet(fichier_serie)
    disponibles = sorted(int(f.stem.removeprefix("prix_")) for f in historique.glob("prix_*.parquet"))
    # Une année n'est recalculée qu'avec la précédente (prix de décembre prolongés en janvier), sauf la première
    annees = [a for a in disponibles if a - 1 in disponibles or a == 2007 or ancienne is None and a == disponibles[0]]
    serie = suivi.fusionner_serie(ancienne, suivi.serie_nationale(historique, annees))
    journal = pl.read_parquet(fichier_journal) if fichier_journal.exists() and not complet else None
    marches_ = pl.read_parquet(dossier / "marches" / "brent_change.parquet")
    aujourd_hui = date.today()
    journal = suivi.prevoir(serie, marches_, journal, aujourd_hui, retro_depuis=date(aujourd_hui.year, 1, 1))
    cible.mkdir(parents=True, exist_ok=True)
    serie.write_parquet(fichier_serie)
    journal.write_parquet(fichier_journal)
    evalue = suivi.evaluer(journal, serie)
    bilan = suivi.bilan(evalue)
    suivi.ecrire_json(bilan, cible / "bilan.json")
    prochaine = journal.row(-1, named=True)
    print(f"Série : {serie.height} semaines jusqu'au {serie['semaine'].max()} ; prévision pour la semaine du "
          f"{prochaine['semaine']} : {prochaine['prevision']:.3f} €/L (dernier prix {prochaine['prix_base']:.3f}) ; "
          f"erreur récente {bilan['erreur_recente']} c (persistance {bilan['erreur_persistance_recente']} c)")
    if prochaine["faite_le"] != aujourd_hui:
        print(f"::warning title=Prévision::Pas de prévision en direct : la série s'arrête au {serie['semaine'].max()}, "
              "l'historique des prix est en retard.")
    if bilan["derive"]:
        print(f"::warning title=Prévision::En direct, la prévision se trompe de {bilan['erreur_directe']} c en moyenne, "
              f"nettement plus que la persistance ({bilan['erreur_persistance_directe']} c) : le modèle est à revoir.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="carburants")
    parser.add_argument("--dossier", type=Path, default=Path("data"), help="dossier de sortie (défaut : data)")
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("instantane", help="photo du jour des prix de toutes les stations")
    annee = sous.add_parser("annee", help="historique complet des prix d'une année")
    annee.add_argument("annee", type=int)
    sous.add_parser("irve", help="fichier consolidé des bornes de recharge")
    sous.add_parser("enseignes", help="enseigne des stations, via OpenStreetMap")
    sous.add_parser("marches", help="cours du Brent et taux euro/dollar (FRED)")
    sous.add_parser("bulletin", help="historique du bulletin pétrolier européen (prix avec et sans taxes)")
    sous.add_parser("territoires", help="communes, voitures par type de recharge, prix de l'électricité")
    expl = sous.add_parser("explication", help="décomposer le prix de chaque station avec le modèle du notebook 05")
    expl.add_argument("--annee", type=int, default=2025)
    expl.add_argument("--sortie", type=Path, default=Path("rapports/explication_stations.json"))
    sous.add_parser("controler", help="contrôler la collecte du jour (rapport dans qualite/)")
    suivre = sous.add_parser("suivi", help="série hebdomadaire du gazole et prévision de la semaine suivante (suivi/)")
    suivre.add_argument("--historique", type=Path, default=Path("data/historique"), help="fichiers prix_AAAA.parquet")
    suivre.add_argument("--complet", action="store_true", help="recalculer toute la série et repartir d'un journal vide")
    page = sous.add_parser("site", help="assembler le site web (pages, figures et données du jour)")
    page.add_argument("--sortie", type=Path, default=Path("_site"), help="dossier du site (défaut : _site)")
    args = parser.parse_args()

    if args.commande == "instantane":
        collecter_instantane(args.dossier)
    elif args.commande == "annee":
        collecter_annee(args.dossier, args.annee)
    elif args.commande == "irve":
        collecter_irve(args.dossier)
    elif args.commande == "marches":
        collecter_marches(args.dossier)
    elif args.commande == "bulletin":
        collecter_bulletin(args.dossier)
    elif args.commande == "explication":
        expliquer_stations(args.dossier, args.annee, args.sortie)
    elif args.commande == "controler":
        controler_collecte(args.dossier)
    elif args.commande == "suivi":
        suivre_prevision(args.dossier, args.historique, args.complet)
    elif args.commande == "site":
        construire_site(args.dossier, args.sortie)
    elif args.commande == "territoires":
        collecter_territoires(args.dossier)
    else:
        collecter_enseignes(args.dossier)


if __name__ == "__main__":
    main()
