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
carburants enseignes      # enseigne de chaque station via OpenStreetMap -> data/enseignes/
```

Chaque collecte produit des fichiers Parquet : `stations.parquet` (une ligne par station) et `prix.parquet` (une ligne par prix publié, avec sa date de mise à jour).

Le workflow [Collecte quotidienne](.github/workflows/collecte.yml) lance la photo des prix chaque matin, les bornes et les enseignes chaque lundi, puis publie les fichiers sur la branche `data`.

Le flux des prix ne donne pas l'enseigne des stations : `carburants enseignes` rapproche chaque station de la station OpenStreetMap la plus proche (à moins de 250 m) et normalise la marque (« Total Access » → TotalEnergies, « Super U » → Système U...), avec un indicateur `grande_surface`.

L'historique complet (un fichier par année depuis 2007, chaque changement de prix) est trop volumineux pour la branche `data`. Le workflow [Historique des prix](.github/workflows/historique.yml) le publie dans la release [`historique`](../../releases/tag/historique) et recharge l'année en cours chaque dimanche :

```bash
gh release download historique --dir data/historique --pattern "prix_202*.parquet"
```

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
