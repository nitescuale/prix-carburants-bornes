# Faire le plein ou recharger ?

[![Tests](https://github.com/nitescuale/prix-carburants-bornes/actions/workflows/ci.yml/badge.svg)](https://github.com/nitescuale/prix-carburants-bornes/actions/workflows/ci.yml)
[![Collecte quotidienne](https://github.com/nitescuale/prix-carburants-bornes/actions/workflows/collecte.yml/badge.svg)](https://github.com/nitescuale/prix-carburants-bornes/actions/workflows/collecte.yml)

Projet de data science de bout en bout sur les données publiques françaises : prix des carburants à la pompe et bornes de recharge pour véhicules électriques.

## Données

| Source | Contenu | Licence |
|---|---|---|
| [Prix des carburants](https://www.prix-carburants.gouv.fr/rubrique/opendata/) (ministère de l'Économie) | Prix de chaque station, flux instantané et historique annuel depuis 2007 | Licence Ouverte |
| [Bornes de recharge IRVE](https://www.data.gouv.fr/fr/datasets/fichier-consolide-des-bornes-de-recharge-pour-vehicules-electriques/) (data.gouv.fr) | Points de recharge, puissance, opérateur, tarifs | Licence Ouverte |
| [FRED](https://fred.stlouisfed.org/series/DCOILBRENTEU) (Réserve fédérale de Saint-Louis) | Brent daté (DCOILBRENTEU) et taux dollar/euro (DEXUSEU), quotidiens | Domaine public (sources EIA et Fed) |
| [Bulletin pétrolier](https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en) (Commission européenne) | Prix moyens hebdomadaires à la pompe par pays, avec et sans taxes | CC BY 4.0 (politique de réutilisation de la Commission) |
| [Découpage administratif](https://geo.api.gouv.fr/decoupage-administratif) (geo.api.gouv.fr) | Communes et arrondissements municipaux : population légale, surface, centre | Licence Ouverte |
| [Voitures par commune et type de recharge](https://www.data.gouv.fr/fr/datasets/?q=voitures%20particuli%C3%A8res%20immatricul%C3%A9es%20par%20commune%20et%20par%20type%20de%20recharge) (Agence ORE, d'après AAA Data, sur data.gouv.fr) | Parc de voitures particulières par commune : électriques, hybrides rechargeables, thermiques | Licence Ouverte |
| [Prix de l'électricité des ménages](https://ec.europa.eu/eurostat/databrowser/view/nrg_pc_204/default/table) (Eurostat, nrg_pc_204) | Prix semestriel du kWh, toutes taxes, ménages consommant 2 500 à 5 000 kWh par an | CC BY 4.0 |

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
carburants marches        # Brent ($/baril, €/litre) et euro/dollar, quotidiens (FRED) -> data/marches/
carburants bulletin       # historique du bulletin pétrolier européen (prix avec et sans taxes) -> data/marches/
carburants territoires    # communes, voitures par type de recharge (fichiers bruts), prix de l'électricité -> data/territoires/
```

Chaque collecte produit des fichiers Parquet : `stations.parquet` (une ligne par station) et `prix.parquet` (une ligne par prix publié, avec sa date de mise à jour).

Le workflow [Collecte quotidienne](.github/workflows/collecte.yml) lance la photo des prix et les cours du Brent chaque matin, les bornes, les enseignes, le bulletin pétrolier et les données territoriales chaque lundi, puis publie les fichiers sur la branche `data`.

### Dictionnaire des données

`stations.parquet`

| Colonne | Contenu |
|---|---|
| `id` | Identifiant de la station dans le flux |
| `latitude`, `longitude` | Degrés WGS84 (publiés ×100 000 ; null si absents ou nuls). Souvent arrondis à 3 décimales, soit environ 100 m |
| `cp`, `adresse`, `ville` | Adresse déclarée |
| `type_route` | `autoroute` ou `route` |
| `automate_24_24` | Paiement par automate 24 h/24 |
| `services` | Liste des services déclarés |
| `fermeture_type`, `fermeture_debut`, `fermeture_fin` | Fermeture déclarée : `T` temporaire, `D` définitive (texte brut du flux) |
| `ruptures` | Carburants en rupture de stock sans date de fin |

`prix.parquet`

| Colonne | Contenu |
|---|---|
| `station_id` | Identifiant de la station |
| `carburant`, `carburant_id` | Gazole (1), SP95 (2), E85 (3), GPLc (4), E10 (5), SP98 (6) |
| `maj` | Date et heure de mise à jour du prix, **heure de Paris sans fuseau** |
| `prix` | Euros par litre (les fichiers anciens publient des millièmes, convertis) |

Contrôles appliqués à la lecture : prix hors de [0,20 ; 4,00] €/L écartés (quelques centaines par an, saisies du type 9,999 €), un seul prix par station, carburant et minute (le dernier publié).

Limites connues :
- dans les fichiers 2015 et 2016, la minute de `maj` est souvent égale au mois (« 01:04 » en avril) : l'heure de ces années n'est fiable qu'au jour près ;
- environ un quart des prix de la photo du jour ont plus de 7 jours, et quelques centaines de stations n'ont plus aucun prix récent : il faut filtrer sur `maj` pour parler de prix « actuels » ;
- la photo quotidienne est prise vers 7 h (heure de Paris) ; la branche `data` évolue, un notebook reproductible doit noter le commit utilisé.

Le flux des prix ne donne pas l'enseigne des stations : `carburants enseignes` rapproche chaque station de la station OpenStreetMap la plus proche (à moins de 250 m) et normalise la marque (« Total Access » → TotalEnergies, « Super U » → Système U...), avec un indicateur `grande_surface`. Le nom OSM ne sert que s'il commence par une enseigne connue, et `osm_partage` signale deux stations du flux rattachées à la même station OSM. Si Overpass est indisponible, le workflow publie quand même les prix et affiche un avertissement.

L'historique complet (un fichier par année depuis 2007, chaque changement de prix) est trop volumineux pour la branche `data`. Le workflow [Historique des prix](.github/workflows/historique.yml) le publie dans la release [`historique`](../../releases/tag/historique) et recharge l'année en cours chaque dimanche. Chaque année a une fiche `prix_AAAA.json` (commit qui l'a produite, nombre de lignes, dates extrêmes) :

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
| [02_historique](notebooks/02_historique.ipynb) | Vingt ans de prix (2007-2026) : record de 2026, écart gazole / essence, prix identiques de TotalEnergies, géographie, jour de la semaine ; chiffres cités vérifiés par le notebook et exportés dans `rapports/chiffres_02.json` |
| [03_fusee_et_plume](notebooks/03_fusee_et_plume.ipynb) | Du baril à la pompe (2005-2026) : décomposition du prix, marge brute, effet « fusée et plume » (modèle à correction d'erreur asymétrique, `carburants.transmission`), crise de 2026 ; chiffres vérifiés et exportés dans `rapports/chiffres_03.json` |
| [04_concurrence](notebooks/04_concurrence.ipynb) | La concurrence fait-elle baisser les prix ? Enseigne (dont Total Access), nombre de concurrents et grande surface voisine (régression à effets fixes), ouvertures de grandes surfaces (différence de différences avec placebo, `carburants.concurrence`) ; chiffres vérifiés et exportés dans `rapports/chiffres_04.json` |

Le notebook 02 lit l'historique de la release (`gh release download historique --dir data/historique`) et le reconstruit en série hebdomadaire avec `carburants.analyse.prix_hebdo` : prévoir environ 6 Go de mémoire.

Le notebook 03 lit aussi les cours du Brent et le bulletin pétrolier européen sur la branche `data` (`data/marches/`) ; il demande `statsmodels` et `openpyxl` (extra `analyse`). Le notebook 04 lit l'historique et le relevé des enseignes OpenStreetMap (`data/enseignes/`).

Les graphiques exportés pour le site sont dans [`rapports/figures`](rapports/figures).

## Licence

Code sous licence MIT. Les données restent sous Licence Ouverte (Etalab) et ODbL pour OpenStreetMap.
