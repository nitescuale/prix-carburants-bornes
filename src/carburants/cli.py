"""Ligne de commande : `carburants instantane`, `carburants annee 2024`, `carburants irve`, `carburants enseignes`,
`carburants marches`, `carburants bulletin`."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import polars as pl

from carburants import enseignes, irve, marches, prix


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
    else:
        collecter_enseignes(args.dossier)


if __name__ == "__main__":
    main()
