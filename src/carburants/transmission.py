"""Transmission du prix du pétrole au prix à la pompe : « fusée et plume ».

Le prix monte-t-il comme une fusée quand le pétrole augmente, et redescend-il comme une plume quand il baisse ?
On l'estime avec un modèle à correction d'erreur asymétrique (Borenstein, Cameron et Gilbert, 1997) sur des séries
hebdomadaires :

    Δp_t = Σ_i (a⁺_i Δc⁺_{t-i} + a⁻_i Δc⁻_{t-i}) + Σ_j g_j Δp_{t-j} + θ (p_{t-1} - φ c_{t-1} - f(t-1)) + ε_t

où p est le prix hors taxes, c le coût du pétrole brut (en euros par litre), Δc⁺ et Δc⁻ ses hausses et ses baisses,
et f une tendance lente (spline) qui laisse la marge de long terme évoluer. Les réponses cumulées à une hausse et à
une baisse durables du pétrole se déduisent des coefficients par simulation.

Les fonctions d'estimation demandent statsmodels (extra « analyse »).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl

# Remise carburant de l'État, en euros par litre hors taxes : 18 centimes TTC du 1er avril au 31 août 2022, 30 du
# 1er septembre au 15 novembre, 10 du 16 novembre au 31 décembre. Le bulletin pétrolier publie des prix remise
# déduite, relevés en début de semaine (le lundi) : celui du 2 janvier 2023 décrit encore les prix de fin décembre.
REMISES_2022 = [
    (date(2022, 4, 4), date(2022, 8, 29), 0.15),
    (date(2022, 9, 5), date(2022, 11, 14), 0.25),
    (date(2022, 11, 21), date(2023, 1, 2), 0.10 / 1.2),
]


def remise_etat(colonne: str = "date") -> pl.Expr:
    """La remise de l'État en vigueur le lundi de la semaine (0 hors de 2022)."""
    expression = pl.lit(0.0)
    for debut, fin, montant in REMISES_2022:
        expression = pl.when(pl.col(colonne).is_between(debut, fin)).then(montant).otherwise(expression)
    return expression


def brent_hebdo(marches: pl.DataFrame) -> pl.DataFrame:
    """Le cours moyen des sept jours qui précèdent chaque lundi, date des relevés du bulletin pétrolier."""
    jours_avant_lundi = 8 - pl.col("date").dt.weekday()  # du lundi (1) au dimanche (7) : le lundi suivant
    return (
        marches.with_columns(lundi=pl.col("date") + pl.duration(days=jours_avant_lundi))
        .group_by("lundi")
        .agg(pl.col("brent_usd_baril", "brent_eur_litre").mean(), jours=pl.len())
        .rename({"lundi": "date"})
        .sort("date")
    )


@dataclass
class Transmission:
    """Un modèle estimé : coefficients, covariance robuste et relation de long terme."""

    noms: list[str]
    coefs: np.ndarray
    covariance: np.ndarray
    phi: float
    retards_cout: int
    retards_prix: int
    observations: int
    r2: float
    adf: float = float("nan")
    resultat: object = field(repr=False, default=None)

    def coef(self, nom: str) -> float:
        return float(self.coefs[self.noms.index(nom)])

    def ecart_type(self, nom: str) -> float:
        return float(np.sqrt(self.covariance[self.noms.index(nom), self.noms.index(nom)]))

    def reponse(self, signe: float = 1.0, horizon: int = 12, coefs: np.ndarray | None = None) -> np.ndarray:
        """Variation cumulée du prix, semaine par semaine, après une variation durable de `signe` du coût à t = 0."""
        b = dict(zip(self.noms, self.coefs if coefs is None else coefs))
        K, J = self.retards_cout, self.retards_prix
        prix = np.zeros(horizon + 1)
        variations = np.zeros(horizon + 1)
        for t in range(horizon + 1):
            # Le coût ne varie qu'à t = 0 : la variation retardée de t semaines n'agit qu'au retard t
            dp = b[f"hausse_{t}"] * max(signe, 0) + b[f"baisse_{t}"] * min(signe, 0) if t <= K else 0.0
            dp += sum(b[f"prix_{j}"] * variations[t - j] for j in range(1, J + 1) if t - j >= 0)
            if t > 0:
                dp += b["rappel"] * (prix[t - 1] - self.phi * signe)
            variations[t] = dp
            prix[t] = (prix[t - 1] if t else 0.0) + dp
        return prix

    def asymetrie(self, horizon: int = 12, tirages: int = 2000, graine: int = 0) -> dict[str, np.ndarray]:
        """Réponses à +1 et à -1 (cette dernière changée de signe) et l'écart, avec un intervalle à 90 %.

        L'incertitude vient de tirages des coefficients dans leur loi estimée (méthode de Krinsky et Robb).
        """
        tirage = np.random.default_rng(graine).multivariate_normal(self.coefs, self.covariance, tirages)
        simulations = {
            "hausse": np.array([self.reponse(1, horizon, b) for b in tirage]),
            "baisse": np.array([-self.reponse(-1, horizon, b) for b in tirage]),
        }
        simulations["ecart"] = simulations["hausse"] - simulations["baisse"]
        resultat = {"hausse": self.reponse(1, horizon), "baisse": -self.reponse(-1, horizon)}
        resultat["ecart"] = resultat["hausse"] - resultat["baisse"]
        for nom, valeurs in simulations.items():
            resultat[f"{nom}_bas"], resultat[f"{nom}_haut"] = np.percentile(valeurs, [5, 95], axis=0)
        return resultat

    def test_wald(self, retards: list[int] | None = None, cumule: bool = True) -> tuple[float, float]:
        """Test d'égalité des réactions aux hausses et aux baisses : (statistique du χ², p-valeur).

        `cumule` teste l'égalité des sommes sur les retards ; sinon, l'égalité retard par retard (test joint).
        """
        retards = list(range(self.retards_cout + 1)) if retards is None else retards
        lignes = []
        for i in retards:
            ligne = np.zeros(len(self.noms))
            ligne[self.noms.index(f"hausse_{i}")], ligne[self.noms.index(f"baisse_{i}")] = 1, -1
            lignes.append(ligne)
        R = np.sum(lignes, axis=0, keepdims=True) if cumule else np.array(lignes)
        ecart = R @ self.coefs
        stat = float(ecart @ np.linalg.solve(R @ self.covariance @ R.T, ecart))
        from scipy.stats import chi2

        return stat, float(chi2.sf(stat, R.shape[0]))


def estimer(
    serie: pl.DataFrame,
    retards_cout: int = 4,
    retards_prix: int = 2,
    tendance_df: int = 4,
    retards_hac: int = 4,
    exclure: tuple[date, date] | None = None,
    combler_trous: bool = False,
) -> Transmission:
    """Estime le modèle sur une série hebdomadaire `date`, `prix`, `cout` (les semaines manquantes sont tolérées).

    La relation de long terme est estimée d'abord (Engle et Granger), puis la dynamique de court terme par moindres
    carrés, avec des écarts-types robustes à l'autocorrélation (Newey et West). `exclure` retire la période donnée
    de la relation de long terme, et de la dynamique toutes les semaines dont la fenêtre de retards la touche.
    `combler_trous` interpole linéairement les semaines manquantes au lieu de perdre les semaines qui les suivent.
    La statistique `adf` (Dickey-Fuller augmenté sur les résidus de long terme) vérifie la cointégration.
    """
    import statsmodels.api as sm
    from patsy import dmatrix
    from statsmodels.tsa.stattools import adfuller

    if serie.is_empty():
        raise ValueError("Série vide")
    K, J = retards_cout, retards_prix
    grille = pl.DataFrame({"date": pl.date_range(serie["date"].min(), serie["date"].max(), "1w", eager=True)})
    hors_grille = serie.join(grille, on="date", how="anti")
    if not hors_grille.is_empty():
        raise ValueError(f"{hors_grille.height} dates hors de la grille hebdomadaire (première : {hors_grille['date'][0]})")
    x = grille.join(serie.select("date", "prix", "cout"), on="date", how="left")
    if combler_trous:
        x = x.with_columns(pl.col("prix", "cout").interpolate())
    if exclure is not None:
        debut, fin = exclure
        fenetre = pl.duration(weeks=max(K, J) + 1)
        dans_exclusion = pl.col("date").is_between(debut, fin)
        touche_exclusion = pl.col("date").is_between(debut, pl.lit(fin) + fenetre)
    else:
        dans_exclusion = touche_exclusion = pl.lit(False)

    complet = x.drop_nulls(["prix", "cout"]).filter(~dans_exclusion)
    annees = (complet["date"] - complet["date"].min()).dt.total_days().to_numpy() / 365.25
    if tendance_df:
        tendance = np.asarray(dmatrix(f"bs(t, df={tendance_df}, include_intercept=True) - 1", {"t": annees}))
    else:
        tendance = np.ones((complet.height, 1))
    long_terme = sm.OLS(complet["prix"].to_numpy(), np.column_stack([tendance, complet["cout"].to_numpy()])).fit()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)  # format de retour d'adfuller qui évolue dans statsmodels
        adf = adfuller(long_terme.resid, maxlag=4, autolag=None)[0]
    x = x.join(complet.select("date").with_columns(ecart=pl.Series(long_terme.resid)), on="date", how="left")

    x = x.with_columns(dp=pl.col("prix").diff(), dc=pl.col("cout").diff())
    retards = {}
    for i in range(K + 1):
        retards[f"hausse_{i}"] = pl.col("dc").clip(lower_bound=0).shift(i)
        retards[f"baisse_{i}"] = pl.col("dc").clip(upper_bound=0).shift(i)
    for j in range(1, J + 1):
        retards[f"prix_{j}"] = pl.col("dp").shift(j)
    retards["rappel"] = pl.col("ecart").shift(1)
    x = x.with_columns(**retards).filter(~touche_exclusion).drop_nulls(["dp", *retards])

    noms = ["constante", *retards]
    X = np.column_stack([np.ones(x.height), x.select(list(retards)).to_numpy()])
    court_terme = sm.OLS(x["dp"].to_numpy(), X).fit(cov_type="HAC", cov_kwds={"maxlags": retards_hac})
    return Transmission(
        noms=noms,
        coefs=np.asarray(court_terme.params),
        covariance=np.asarray(court_terme.cov_params()),
        phi=float(long_terme.params[-1]),
        retards_cout=K,
        retards_prix=J,
        observations=x.height,
        r2=float(court_terme.rsquared),
        adf=float(adf),
        resultat=court_terme,
    )
