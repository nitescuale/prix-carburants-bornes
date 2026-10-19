# Faire le plein ou recharger ?

Projet de data science de bout en bout sur les données publiques françaises : prix des carburants à la pompe et bornes de recharge pour véhicules électriques.

## Données

| Source | Contenu | Licence |
|---|---|---|
| [Prix des carburants](https://www.prix-carburants.gouv.fr/rubrique/opendata/) (ministère de l'Économie) | Prix de chaque station, flux instantané et historique annuel depuis 2007 | Licence Ouverte |
| [Bornes de recharge IRVE](https://www.data.gouv.fr/fr/datasets/fichier-consolide-des-bornes-de-recharge-pour-vehicules-electriques/) (data.gouv.fr) | Points de recharge, puissance, opérateur, tarifs | Licence Ouverte |

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Collecte

```bash
carburants instantane     # photo du jour des prix -> data/prix/instantane/date=AAAA-MM-JJ/
carburants annee 2024     # historique complet d'une année -> data/prix/historique/annee=2024/
carburants irve           # bornes de recharge -> data/irve/bornes_AAAA-MM-JJ.parquet
```

Chaque collecte produit des fichiers Parquet : `stations.parquet` (une ligne par station) et `prix.parquet` (une ligne par prix publié, avec sa date de mise à jour).

Le workflow [Collecte quotidienne](.github/workflows/collecte.yml) lance la photo des prix chaque matin et les bornes chaque lundi, puis publie les fichiers sur la branche `data`.

## Analyses

```bash
pip install -e ".[analyse]"
git fetch origin data && git worktree add data origin/data   # les données collectées, dans data/
jupyter lab notebooks/
```

| Notebook | Contenu |
|---|---|
| [01_exploration](notebooks/01_exploration.ipynb) | Prix par carburant, autoroute contre route, géographie, concurrence locale, bornes de recharge, qualité des données |

Les graphiques exportés pour le site sont dans [`rapports/figures`](rapports/figures).
