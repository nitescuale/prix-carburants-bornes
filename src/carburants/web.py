"""Les données de l'application web : la photo du jour des prix, les sites de recharge rapide, les communes et un
résumé des coûts aux 100 km.

Le site (dossier `site/`) est statique : des pages HTML qui lisent ces fichiers JSON, sans serveur. `construire`
assemble le tout dans un dossier prêt à publier sur GitHub Pages ; la publication tourne après chaque collecte.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from carburants import bornes

CARBURANTS = ["Gazole", "E10", "SP95", "SP98", "E85", "GPLc"]
PETITS_MOTS = ["de", "du", "des", "la", "le", "les", "en", "sur", "sous", "lès", "lez", "et", "aux", "au"]
FRAICHEUR_JOURS = 8  # un prix plus ancien n'est pas montré : la station a pu en changer sans le déclarer

# Consommations moyennes retenues dans le notebook 06 (litres ou kWh aux 100 km)
CONSO = {"Gazole": 5.5, "E10": 6.5, "electrique": 18.0}
# Classes de puissance des tarifs publics, comme dans le notebook 06
CLASSES_TARIFS = {"lente": "7,4 à 22 kW", "rapide": "50 à 150 kW", "ultra": "150 kW et plus"}


def dernier(dossier: Path, motif: str) -> Path | None:
    """Le fichier (ou dossier) le plus récent qui correspond au motif ; les noms datés se trient dans l'ordre."""
    trouves = sorted(dossier.glob(motif))
    return trouves[-1] if trouves else None


def majuscules_adoucies(texte: pl.Expr) -> pl.Expr:
    """« SAINT-DENIS-LèS-BOURG » → « Saint-Denis-Lès-Bourg » : un texte surtout en majuscules passe en casse de titre,
    les autres restent tels quels."""
    majuscules = texte.str.count_matches(r"\p{Lu}")
    minuscules = texte.str.count_matches(r"\p{Ll}")
    titre = texte.str.to_titlecase()
    for mot in PETITS_MOTS:  # « Bourg-En-Bresse » → « Bourg-en-Bresse »
        for avant, apres in ["--", "  ", "- ", " -"]:
            titre = titre.str.replace_all(f"{avant}{mot.title()}{apres}", f"{avant}{mot}{apres}", literal=True)
    return pl.when(majuscules > minuscules).then(titre).otherwise(texte)


def prix_du_jour(stations: pl.DataFrame, prix: pl.DataFrame, jour: date,
                 enseignes: pl.DataFrame | None = None) -> pl.DataFrame:
    """Une ligne par station de métropole, avec le prix de chaque carburant mis à jour depuis moins de
    `FRAICHEUR_JOURS` jours (null sinon). Les stations sans aucun prix récent sont écartées."""
    recents = prix.filter(
        pl.col("carburant").is_in(CARBURANTS),
        pl.col("maj").dt.date() > jour - timedelta(days=FRAICHEUR_JOURS),
    ).sort("maj")  # « last » ci-dessous : le prix le plus récent
    larges = recents.pivot("carburant", index="station_id", values="prix", aggregate_function="last")
    for carburant in CARBURANTS:
        if carburant not in larges.columns:
            larges = larges.with_columns(pl.lit(None, dtype=pl.Float64).alias(carburant))
    maj = recents.group_by("station_id").agg(maj=pl.col("maj").max().dt.date())
    table = (
        stations.rename({"id": "station_id"})
        .filter(bornes.en_metropole(pl.col("longitude"), pl.col("latitude")).fill_null(False))
        .join(larges.select("station_id", *CARBURANTS), on="station_id")
        .join(maj, on="station_id")
    )
    if enseignes is not None:
        table = table.join(enseignes.select("station_id", "enseigne"), on="station_id", how="left")
    else:
        table = table.with_columns(enseigne=pl.lit(None, dtype=pl.Utf8))
    return table.select(
        "station_id", "latitude", "longitude",
        majuscules_adoucies(pl.col("adresse")).alias("adresse"), majuscules_adoucies(pl.col("ville")).alias("ville"),
        "cp", "enseigne",
        (pl.col("type_route") == "autoroute").alias("autoroute"),
        pl.col("automate_24_24").fill_null(False).alias("automate"),
        *CARBURANTS, "maj",
    ).sort("station_id")


def sites_rapides(points: pl.DataFrame) -> pl.DataFrame:
    """Les sites de recharge rapide : stations voisines de moins de 150 m regroupées, avec leurs opérateurs, la
    puissance maximale, le nombre de points de charge rapides (50 kW et plus) et leur tarif médian lisible (€/kWh).
    Les points lents d'une même station (et leurs tarifs) sont laissés de côté."""
    rapides = points.filter(
        pl.col("metropole"), ~pl.col("acces_reserve").fill_null(False), pl.col("puissance_kw") >= bornes.RAPIDE_KW
    )
    stations = bornes.stations_recharge(rapides)
    tarifs = rapides.group_by("id_station").agg(prix_kwh=pl.col("prix_kwh").median())
    stations = stations.join(tarifs, on="id_station", how="left")
    stations = stations.with_columns(site=pl.Series(bornes.sites(stations)))
    return (
        stations.group_by("site")
        .agg(
            pl.col("latitude", "longitude").mean(),
            operateurs=pl.col("operateur").drop_nulls().unique().sort(),
            puissance_max_kw=pl.col("puissance_max_kw").max(),
            points_de_charge=pl.col("points_de_charge").sum(),
            prix_kwh=pl.col("prix_kwh").median(),
        )
        .with_columns(operateurs=pl.col("operateurs").list.join(" · "))
        .sort("latitude", "longitude")
        .drop("site")
    )


def communes_metropole(communes: pl.DataFrame) -> pl.DataFrame:
    """Les communes de métropole et leur centre, les plus peuplées d'abord (pour l'autocomplétion du trajet)."""
    return (
        communes.filter(
            pl.col("type") == "commune-actuelle", pl.col("latitude").is_not_null(),
            bornes.en_metropole(pl.col("longitude"), pl.col("latitude")),
        )
        .select("nom", "departement", "latitude", "longitude", pl.col("population").fill_null(0))
        .sort(["population", "nom"], descending=[True, False])
    )


def resume(jour_prix: date, prix: pl.DataFrame, points: pl.DataFrame | None, jour_bornes: date | None,
           electricite: pl.DataFrame | None, nb_sites_rapides: int) -> dict:
    """Les chiffres du jour : prix médians, tarifs de l'électricité et coût de l'énergie aux 100 km."""
    carburants = {}
    for carburant in CARBURANTS:
        valeurs = prix[carburant].drop_nulls()
        if valeurs.is_empty():
            continue
        carburants[carburant] = {
            "mediane": round(float(valeurs.median()), 3),
            "p10": round(float(valeurs.quantile(0.1)), 3),
            "p90": round(float(valeurs.quantile(0.9)), 3),
            "stations": valeurs.len(),
        }
    kwh = {}
    if electricite is not None and not electricite.is_empty():
        derniere = electricite.sort("debut").row(-1, named=True)
        kwh["domicile"] = {"prix": round(derniere["prix_eur_kwh"], 4), "semestre": derniere["semestre"]}
    if points is not None:
        publics = points.filter(pl.col("metropole"), ~pl.col("acces_reserve").fill_null(False))
        for nom, classe in CLASSES_TARIFS.items():
            tarif, operateurs = bornes.tarif_median(publics.filter(pl.col("classe") == classe))
            if tarif is not None:
                kwh[nom] = {"prix": round(tarif, 3), "operateurs": operateurs}
    cout = {c: round(carburants[c]["mediane"] * CONSO[c], 2) for c in ["Gazole", "E10"] if c in carburants}
    cout |= {nom: round(t["prix"] * CONSO["electrique"], 2) for nom, t in kwh.items()}
    return {
        "date_prix": jour_prix.isoformat(),
        "date_bornes": jour_bornes.isoformat() if jour_bornes else None,
        "stations": prix.height,
        "sites_rapides": nb_sites_rapides,
        "carburants": carburants,
        "electricite": kwh,
        "conso": CONSO,
        "cout_100km": cout,
    }


def colonnes(table: pl.DataFrame, decimales: dict[str, int]) -> dict:
    """Une table en JSON compact : la liste des colonnes, puis une liste de lignes (plus léger qu'un objet par ligne)."""
    table = table.with_columns(
        [pl.col(c).round(d) for c, d in decimales.items()]
        + [pl.col(c).cast(pl.Utf8) for c, t in table.schema.items() if t == pl.Date]
    )
    return {"colonnes": table.columns, "lignes": table.rows()}


def ecrire_json(objet, chemin: Path) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(objet, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def donnees(dossier: Path, sortie: Path) -> dict:
    """Écrit les fichiers JSON du site dans `sortie` à partir du dossier de données (branche data) ; renvoie le résumé."""
    photo = dernier(dossier / "prix" / "instantane", "date=*")
    if photo is None:
        raise SystemExit("Aucune photo des prix : lancer d'abord `carburants instantane`")
    jour_prix = date.fromisoformat(photo.name.removeprefix("date="))
    fichier_enseignes = dernier(dossier / "enseignes", "stations_enseignes_*.parquet")
    prix = prix_du_jour(
        pl.read_parquet(photo / "stations.parquet"), pl.read_parquet(photo / "prix.parquet"), jour_prix,
        pl.read_parquet(fichier_enseignes) if fichier_enseignes else None,
    )
    decimales = {"latitude": 5, "longitude": 5} | {c: 3 for c in CARBURANTS}
    ecrire_json({"date": jour_prix.isoformat()} | colonnes(prix, decimales), sortie / "stations.json")

    points, jour_bornes = None, None
    rapides = pl.DataFrame(schema={"latitude": pl.Float64, "longitude": pl.Float64, "operateurs": pl.Utf8,
                                   "puissance_max_kw": pl.Float64, "points_de_charge": pl.UInt32, "prix_kwh": pl.Float64})
    fichier_irve = dernier(dossier / "irve", "bornes_*.parquet")
    if fichier_irve is not None:
        jour_bornes = date.fromisoformat(fichier_irve.stem.removeprefix("bornes_"))
        points = bornes.nettoyer(pl.read_parquet(fichier_irve), aujourd_hui=jour_bornes)
        rapides = sites_rapides(points)
    # Sans fichier des bornes, une table vide : la carte et le trajet s'affichent quand même
    decimales = {"latitude": 5, "longitude": 5, "puissance_max_kw": 0, "prix_kwh": 3}
    ecrire_json({"date": jour_bornes.isoformat() if jour_bornes else None} | colonnes(rapides, decimales),
                sortie / "recharge.json")

    territoires = dossier / "territoires"
    if (territoires / "communes.parquet").exists():
        communes = communes_metropole(pl.read_parquet(territoires / "communes.parquet"))
        ecrire_json(colonnes(communes, {"latitude": 4, "longitude": 4}), sortie / "communes.json")
    electricite = None
    if (territoires / "prix_electricite.parquet").exists():
        electricite = pl.read_parquet(territoires / "prix_electricite.parquet")

    chiffres = resume(jour_prix, prix, points, jour_bornes, electricite, rapides.height)
    ecrire_json(chiffres, sortie / "resume.json")
    return chiffres


def construire(dossier: Path, sortie: Path, source: Path = Path("site"), rapports: Path = Path("rapports")) -> dict:
    """Le site complet dans `sortie` : les pages de `source`, les figures des notebooks, la décomposition du prix de
    chaque station (`carburants explication`) et les données du jour."""
    if sortie.exists():
        shutil.rmtree(sortie)
    shutil.copytree(source, sortie)
    if (rapports / "figures").exists():
        shutil.copytree(rapports / "figures", sortie / "figures")
    if (rapports / "explication_stations.json").exists():
        (sortie / "donnees").mkdir(parents=True, exist_ok=True)
        shutil.copy(rapports / "explication_stations.json", sortie / "donnees" / "explication.json")
    return donnees(dossier, sortie / "donnees")
