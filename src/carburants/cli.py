"""Ligne de commande : `carburants instantane`, `carburants annee 2024`, `carburants irve`."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from carburants import irve, prix


def collecter_instantane(dossier: Path) -> None:
    stations, tarifs = prix.parser_xml(prix.telecharger_xml(prix.URL_INSTANTANE))
    cible = dossier / "prix" / "instantane" / f"date={date.today().isoformat()}"
    cible.mkdir(parents=True, exist_ok=True)
    stations.write_parquet(cible / "stations.parquet")
    tarifs.write_parquet(cible / "prix.parquet")
    print(f"{len(stations)} stations, {len(tarifs)} prix -> {cible}")


def collecter_annee(dossier: Path, annee: int) -> None:
    stations, tarifs = prix.parser_xml(prix.telecharger_xml(prix.url_annee(annee)))
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="carburants")
    parser.add_argument("--dossier", type=Path, default=Path("data"), help="dossier de sortie (défaut : data)")
    sous = parser.add_subparsers(dest="commande", required=True)
    sous.add_parser("instantane", help="photo du jour des prix de toutes les stations")
    annee = sous.add_parser("annee", help="historique complet des prix d'une année")
    annee.add_argument("annee", type=int)
    sous.add_parser("irve", help="fichier consolidé des bornes de recharge")
    args = parser.parse_args()

    if args.commande == "instantane":
        collecter_instantane(args.dossier)
    elif args.commande == "annee":
        collecter_annee(args.dossier, args.annee)
    else:
        collecter_irve(args.dossier)


if __name__ == "__main__":
    main()
