// Les coulisses : contrôles de la collecte et suivi de la prévision hebdomadaire (donnees/suivi.json).

(async function () {
  const { nombre, euros, signe, echapper, date } = Commun;
  const ICONES = { ok: "✅", alerte: "⚠️", erreur: "❌" };
  const LIBELLES = { ok: "tout va bien", alerte: "à surveiller", erreur: "anomalie" };
  const SVG = "http://www.w3.org/2000/svg";

  let suivi;
  try {
    suivi = await Commun.charger("suivi");
  } catch {
    document.getElementById("controles").innerHTML = '<p class="discret">Le suivi n\'est pas encore disponible : il apparaît après la première collecte contrôlée.</p>';
    document.getElementById("prevision").innerHTML = "";
    return;
  }

  // Les contrôles du jour
  const controle = suivi.dernier_controle;
  if (controle) {
    const lignes = controle.controles
      .map((c) => `<tr><td>${echapper(c.nom)}</td><td class="chiffre">${valeur(c.valeur)}</td><td class="discret">${echapper(c.attendu)}</td><td>${ICONES[c.statut]}</td></tr>`)
      .join("");
    document.getElementById("controles").innerHTML = `
      <p>Collecte du ${date(controle.date)} : <b>${ICONES[controle.statut]} ${LIBELLES[controle.statut]}</b>.</p>
      <div class="tableau-defilant"><table class="sources"><thead><tr><th>Contrôle</th><th>Valeur</th><th>Attendu</th><th></th></tr></thead><tbody>${lignes}</tbody></table></div>`;
  } else {
    document.getElementById("controles").innerHTML = '<p class="discret">Aucun contrôle enregistré pour l\'instant.</p>';
  }
  if (suivi.qualite?.length) {
    const jours = suivi.qualite
      .map((q) => `<span class="jour ${q.statut}" title="${date(q.date)} : ${LIBELLES[q.statut]}, ${nombre(q.stations)} stations, ${nombre(q.part_recente, 1)} % de prix récents"></span>`)
      .join("");
    const alertes = suivi.qualite.filter((q) => q.statut !== "ok").length;
    document.getElementById("historique-qualite").innerHTML = `
      <div class="jours">${jours}</div>
      <p class="petit discret">${suivi.qualite.length} collecte${suivi.qualite.length > 1 ? "s" : ""} contrôlée${suivi.qualite.length > 1 ? "s" : ""}, ${alertes ? `${alertes} avec une alerte ou une anomalie` : "aucune alerte"}. Survolez un jour pour le détail.</p>`;
  }

  function valeur(v) {
    if (v === null || v === undefined) return "–";
    if (typeof v === "number") return nombre(v, Number.isInteger(v) ? 0 : v < 10 ? (Math.abs(v) < 1 ? 2 : 3) : 1);
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return date(v);
    return echapper(v);
  }

  // La prévision
  if (!suivi.previsions) {
    document.getElementById("prevision").innerHTML = '<p class="discret">La première prévision sera publiée après le prochain rechargement de l\'historique (le dimanche).</p>';
    return;
  }
  const previsions = Commun.objets(suivi.previsions);
  const serie = Commun.objets(suivi.serie);
  const prochaine = previsions[previsions.length - 1];
  const variation = (prochaine.prevision - prochaine.prix_base) * 100;
  document.getElementById("prevision").innerHTML = `
    <div class="aujourdhui"><div class="tuiles tuiles-3">
      <div class="tuile"><span class="tuile-libelle">Prix médian du gazole, semaine du ${date(decaler(prochaine.semaine, -7), { day: "numeric", month: "long" })}</span><span class="tuile-valeur">${euros(prochaine.prix_base, 3)}</span></div>
      <div class="tuile"><span class="tuile-libelle">Prévision pour la semaine du ${date(prochaine.semaine, { day: "numeric", month: "long" })}</span><span class="tuile-valeur violet">${euros(prochaine.prevision, 3)}</span><span class="tuile-note">${signe(variation, 1)} centime${Math.abs(variation) >= 2 ? "s" : ""}</span></div>
      <div class="tuile"><span class="tuile-libelle">Prévision faite le</span><span class="tuile-valeur petite">${prochaine.faite_le ? date(prochaine.faite_le, { day: "numeric", month: "long" }) : "–"}</span><span class="tuile-note">${prochaine.mode === "direct" ? "en direct" : "rétrospective"}</span></div>
    </div></div>`;

  const b = suivi.bilan;
  if (b && b.erreur_recente !== null) {
    const gain = (1 - b.erreur_recente / b.erreur_persistance_recente) * 100;
    document.getElementById("bilan").innerHTML = `
      <p>Sur les ${Math.min(12, b.semaines_jugees)} dernières semaines, la prévision s'est trompée en moyenne de <b>${nombre(b.erreur_recente, 1)} centimes</b>, contre ${nombre(b.erreur_persistance_recente, 1)} pour la persistance (${gain >= 0 ? `${nombre(gain, 0)} % de mieux` : `${nombre(-gain, 0)} % de moins bien`})&nbsp;; le notebook 05 mesurait ${nombre(b.erreur_reference, 1)} centimes depuis 2022. ${b.semaines_en_direct ? `${b.semaines_en_direct} de ces semaines ont été prévues en direct.` : "Aucune prévision en direct n'a encore été jugée : les points pleins s'ajouteront chaque dimanche."}
      ${b.derive ? '<span class="alerte" style="display:block;margin-top:8px">Alerte : l\'erreur récente dépasse le double de la référence. Le modèle est peut-être à revoir.</span>' : ""}</p>`;
  }

  function decaler(iso, jours) {
    const d = new Date(`${iso}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + jours);
    return d.toISOString().slice(0, 10);
  }

  function graphique() {
    const conteneur = document.getElementById("graphique-prevision");
    const largeur = Math.max(320, conteneur.clientWidth);
    const hauteur = Math.round(Math.min(380, Math.max(240, largeur * 0.5)));
    const marge = { haut: 16, droite: 16, bas: 28, gauche: 48 };
    const depuis = decaler(prochaine.semaine, -7 * 52);
    const reel = serie.filter((s) => s.semaine >= depuis);
    const prev = previsions.filter((p) => p.semaine >= depuis);
    const temps = (iso) => new Date(`${iso}T00:00:00Z`).getTime();
    const t0 = temps(depuis);
    const t1 = temps(prochaine.semaine);
    const valeurs = [...reel.map((s) => s.prix), ...prev.map((p) => p.prevision)];
    let bas = Math.min(...valeurs);
    let haut = Math.max(...valeurs);
    const marge_y = (haut - bas) * 0.08 || 0.05;
    bas -= marge_y;
    haut += marge_y;
    const x = (iso) => marge.gauche + ((temps(iso) - t0) / (t1 - t0)) * (largeur - marge.gauche - marge.droite);
    const y = (v) => marge.haut + (1 - (v - bas) / (haut - bas)) * (hauteur - marge.haut - marge.bas);

    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", `0 0 ${largeur} ${hauteur}`);
    svg.setAttribute("width", largeur);
    svg.setAttribute("height", hauteur);
    const ajouter = (nom, attributs, texte) => {
      const element = document.createElementNS(SVG, nom);
      for (const [k, v] of Object.entries(attributs)) element.setAttribute(k, v);
      if (texte !== undefined) element.textContent = texte;
      svg.appendChild(element);
      return element;
    };
    // Grille horizontale
    const pas = (haut - bas) > 0.4 ? 0.1 : (haut - bas) > 0.15 ? 0.05 : 0.02;
    for (let v = Math.ceil(bas / pas) * pas; v <= haut; v += pas) {
      ajouter("line", { x1: marge.gauche, x2: largeur - marge.droite, y1: y(v), y2: y(v), stroke: "#e4e3df" });
      ajouter("text", { x: marge.gauche - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "#7a7873" }, nombre(v, 2));
    }
    // Mois sur l'axe horizontal (un sur trois)
    const mois = new Date(t0);
    mois.setUTCDate(1);
    mois.setUTCMonth(mois.getUTCMonth() + 1);
    for (let k = 0; mois.getTime() <= t1; k++, mois.setUTCMonth(mois.getUTCMonth() + 1)) {
      if (k % 3) continue;
      const iso = mois.toISOString().slice(0, 10);
      ajouter("text", { x: x(iso), y: hauteur - 8, "text-anchor": "middle", "font-size": 11, fill: "#7a7873" },
        mois.toLocaleDateString("fr-FR", { month: "short", year: "2-digit", timeZone: "UTC" }));
    }
    // Prix réel
    ajouter("polyline", {
      points: reel.map((s) => `${x(s.semaine)},${y(s.prix)}`).join(" "),
      fill: "none", stroke: "#0b0b0b", "stroke-width": 2, "stroke-linejoin": "round",
    });
    // Prévisions
    for (const p of prev) {
      const direct = p.mode === "direct";
      const cercle = ajouter("circle", {
        cx: x(p.semaine), cy: y(p.prevision), r: direct ? 4.5 : 3,
        fill: direct ? "#8a5cd6" : "#fcfcfb", stroke: direct ? "#fff" : "#8a5cd6", "stroke-width": direct ? 1.5 : 1.2,
      });
      const titre = document.createElementNS(SVG, "title");
      titre.textContent = `Semaine du ${date(p.semaine)} : prévu ${euros(p.prevision, 3)}${p.reel !== null ? `, réel ${euros(p.reel, 3)}` : ""} (${direct ? "en direct" : "rétrospectif"})`;
      cercle.appendChild(titre);
    }
    conteneur.replaceChildren(svg);
  }

  document.getElementById("legende-prevision").innerHTML =
    '<span><i style="background:#0b0b0b;border-radius:2px;height:3px"></i>Prix médian réel</span>' +
    '<span><i style="background:#fcfcfb;border-color:#8a5cd6"></i>Prévision rétrospective</span>' +
    '<span><i style="background:#8a5cd6"></i>Prévision en direct</span>';
  graphique();
  let attente;
  window.addEventListener("resize", () => {
    clearTimeout(attente);
    attente = setTimeout(graphique, 150);
  });
})();
