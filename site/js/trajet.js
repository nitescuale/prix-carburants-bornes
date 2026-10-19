// Simule ton trajet : le coût de l'énergie en thermique et en électrique, avec les stations et les bornes rapides
// qui sont sur la route.

(async function () {
  const { nombre, euros, echapper, distanceKm } = Commun;
  const PROCHE_KM = 2; // une station ou une borne compte si elle est à moins de 2 km de la route
  const RESERVE = 0.1; // on arrive à chaque recharge avec 10 % de batterie…
  const CIBLE = 0.8; // … et on recharge jusqu'à 80 %, au-delà la recharge ralentit
  const DETOUR_VOL_OISEAU = 1.25; // sans itinéraire, la route fait environ 25 % de plus que la ligne droite
  const CONSO_DEFAUT = { Gazole: 5.5, E10: 6.5, SP95: 6.5, SP98: 6.5, E85: 8.0, GPLc: 8.0 };
  const COULEURS = { thermique: "#eb6834", electrique: "#8a5cd6" };

  const carte = L.map("carte", { preferCanvas: true, zoomSnap: 0.5 }).setView([46.6, 2.5], 6);
  Commun.fondDeCarte(carte);
  const calque = L.layerGroup().addTo(carte);
  const champs = (id) => document.getElementById(id);
  const resultat = champs("resultat");

  let resume, stations, sites;
  try {
    const [r, s, b] = await Promise.all([Commun.charger("resume"), Commun.charger("stations"), Commun.charger("recharge")]);
    resume = r;
    stations = Commun.objets(s);
    sites = Commun.objets(b);
  } catch (erreur) {
    resultat.innerHTML = `<p class="alerte">Les données n'ont pas pu être chargées (${echapper(erreur.message)}).</p>`;
    return;
  }

  champs("sous-titre").textContent =
    `Le coût de l'énergie d'un trajet, aux prix du ${Commun.date(resume.date_prix)} des stations et des bornes rapides qui sont sur la route.`;
  for (const [code, libelle] of Object.entries(Commun.CARBURANTS)) {
    if (resume.carburants[code]) champs("carburant").add(new Option(libelle, code));
  }
  champs("carburant").addEventListener("change", () => {
    champs("conso-thermique").value = CONSO_DEFAUT[champs("carburant").value] ?? 6.5;
  });
  // Arrondi au millième, le pas du champ : sinon le navigateur refuse la valeur pré-remplie
  if (resume.electricite.domicile) champs("prix-domicile").value = resume.electricite.domicile.prix.toFixed(3);

  // Communes choisies ; « Paris (75) » et « Lyon (69) » sont proposées d'emblée
  const choix = { depart: null, arrivee: null };
  Commun.autocompletion(champs("depart"), (c) => (choix.depart = c));
  Commun.autocompletion(champs("arrivee"), (c) => (choix.arrivee = c));

  async function retrouver(champ) {
    const texte = champ.value.replace(/\(.*\)/, "");
    const departement = (champ.value.match(/\(([^)]+)\)/) || [])[1];
    const cle = Commun.normaliser(texte);
    const toutes = await Commun.communes();
    return (
      toutes.find((c) => c.cle === cle && (!departement || c.departement === departement)) ||
      toutes.find((c) => c.cle === cle) ||
      toutes.find((c) => c.cle.startsWith(cle))
    );
  }

  /** L'itinéraire routier (OSRM) ; à défaut, la ligne droite allongée de 25 %. */
  async function itineraire(a, b) {
    const url = `https://router.project-osrm.org/route/v1/driving/${a.longitude},${a.latitude};${b.longitude},${b.latitude}?overview=full&geometries=geojson`;
    try {
      const controle = new AbortController();
      const minuterie = setTimeout(() => controle.abort(), 9000);
      const reponse = await fetch(url, { signal: controle.signal });
      clearTimeout(minuterie);
      const donnees = await reponse.json();
      if (donnees.code !== "Ok" || !donnees.routes?.length) throw new Error(donnees.code);
      const route = donnees.routes[0];
      return {
        points: route.geometry.coordinates.map(([lon, lat]) => [lat, lon]),
        km: route.distance / 1000,
        duree: route.duration,
        estime: false,
      };
    } catch {
      const n = 200;
      const points = Array.from({ length: n + 1 }, (_, i) => [
        a.latitude + ((b.latitude - a.latitude) * i) / n,
        a.longitude + ((b.longitude - a.longitude) * i) / n,
      ]);
      return { points, km: distanceKm(a.latitude, a.longitude, b.latitude, b.longitude) * DETOUR_VOL_OISEAU, duree: null, estime: true };
    }
  }

  /** Des repères au plus tous les 500 m le long de la route (y compris au milieu des longs segments), avec leur
   *  distance depuis le départ. */
  function reperes(route) {
    const PAS_KM = 0.5;
    const [la0, lo0] = route.points[0];
    const sortie = [{ lat: la0, lon: lo0, km: 0 }];
    let cumul = 0;
    let dernier = 0;
    for (let i = 1; i < route.points.length; i++) {
      const [la1, lo1] = route.points[i - 1];
      const [la2, lo2] = route.points[i];
      const longueur = distanceKm(la1, lo1, la2, lo2);
      // Un long segment est découpé en morceaux de PAS_KM au plus
      const morceaux = Math.max(1, Math.ceil(longueur / PAS_KM));
      for (let k = 1; k <= morceaux; k++) {
        const f = k / morceaux;
        const km = cumul + longueur * f;
        if (km - dernier >= PAS_KM * 0.999 || (i === route.points.length - 1 && k === morceaux)) {
          sortie.push({ lat: la1 + (la2 - la1) * f, lon: lo1 + (lo2 - lo1) * f, km });
          dernier = km;
        }
      }
      cumul += longueur;
    }
    // Les distances suivent la longueur annoncée de l'itinéraire (ou de l'estimation)
    const facteur = cumul > 0 ? route.km / cumul : 1;
    sortie.forEach((r) => (r.km *= facteur));
    return sortie;
  }

  /** Les objets (stations, bornes) à moins de PROCHE_KM de la route, avec leur position le long du trajet. */
  function leLongDe(objets, jalons) {
    let [minLat, maxLat, minLon, maxLon] = [90, -90, 180, -180];
    for (const j of jalons) {
      minLat = Math.min(minLat, j.lat);
      maxLat = Math.max(maxLat, j.lat);
      minLon = Math.min(minLon, j.lon);
      maxLon = Math.max(maxLon, j.lon);
    }
    const marge = 0.04;
    const cosLat = Math.cos(((minLat + maxLat) / 2) * (Math.PI / 180));
    const retenus = [];
    for (const o of objets) {
      if (o.latitude < minLat - marge || o.latitude > maxLat + marge || o.longitude < minLon - marge / cosLat || o.longitude > maxLon + marge / cosLat) continue;
      let meilleur = Infinity;
      let km = 0;
      for (const j of jalons) {
        const dx = (o.longitude - j.lon) * 111.32 * cosLat;
        const dy = (o.latitude - j.lat) * 110.57;
        const d2 = dx * dx + dy * dy;
        if (d2 < meilleur) {
          meilleur = d2;
          km = j.km;
        }
      }
      const ecart = Math.sqrt(meilleur);
      if (ecart <= PROCHE_KM) retenus.push({ ...o, km, ecart });
    }
    return retenus.sort((a, b) => a.km - b.km);
  }

  function tarif(site) {
    if (site.prix_kwh !== null) return { prix: site.prix_kwh, declare: true };
    const classe = site.puissance_max_kw >= 150 ? "ultra" : "rapide";
    return { prix: resume.electricite[classe]?.prix ?? 0.5, declare: false };
  }

  /** Les arrêts de recharge : on s'arrête dans le dernier tiers de l'autonomie, à la borne la plus puissante (à
   *  défaut, à la plus lointaine), et on recharge à 80 % (ou juste assez pour arriver avec la réserve). */
  function recharges(bornes, km, conso, batterie, chargeDepart) {
    const autonomie = (charge) => ((charge - RESERVE) * batterie) / conso * 100;
    const arrets = [];
    let position = 0;
    let charge = chargeDepart;
    let impossible = false;
    // La marge évite un arrêt fantôme quand la charge vise exactement l'arrivée (erreur d'arrondi)
    while (km - position > autonomie(charge) + 1e-6) {
      const portee = position + autonomie(charge);
      const candidates = bornes.filter((b) => b.km > position + 1 && b.km <= portee);
      if (!candidates.length) {
        impossible = true;
        break;
      }
      const tardives = candidates.filter((b) => b.km >= position + (portee - position) * 0.67);
      const borne = tardives.length
        ? tardives.sort((a, b) => b.puissance_max_kw - a.puissance_max_kw || b.km - a.km)[0]
        : candidates.sort((a, b) => b.km - a.km)[0];
      const arrivee = charge - ((borne.km - position) * conso) / 100 / batterie;
      const besoin = RESERVE + ((km - borne.km) * conso) / 100 / batterie;
      const cible = Math.max(arrivee, Math.min(CIBLE, besoin));
      const energie = Math.max(0, cible - arrivee) * batterie;
      if (energie < 0.1) {
        // Batterie déjà au-dessus de 80 % : on passe cette borne sans s'arrêter
        position = borne.km;
        charge = arrivee;
        continue;
      }
      const { prix, declare } = tarif(borne);
      // Puissance moyenne : la moitié de la puissance de la borne, au plus 75 kW (la recharge ralentit en fin de charge)
      const puissance = Math.min(borne.puissance_max_kw / 2, 75);
      arrets.push({ borne, arrivee, cible, energie, prix, declare, cout: energie * prix, minutes: (energie / puissance) * 60 });
      position = borne.km;
      charge = cible;
    }
    return { arrets, impossible };
  }

  function duree(secondes) {
    const minutes = Math.round(secondes / 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
  }

  function barres(lignes) {
    const max = Math.max(...lignes.map((l) => l.valeur));
    return `<div class="barres">${lignes
      .map(
        (l) =>
          `<div class="barre"><span>${l.libelle}</span><span class="piste"><span class="remplissage" style="left:0;width:${(l.valeur / max) * 100}%;background:${l.couleur};opacity:${l.opacite ?? 1}"></span></span><span class="valeur"><b>${euros(l.valeur)}</b></span></div>`,
      )
      .join("")}</div>`;
  }

  async function calculer() {
    const bouton = champs("calculer");
    bouton.disabled = true;
    bouton.textContent = "Calcul en cours…";
    try {
      const depart = choix.depart && champs("depart").dataset.choisie ? choix.depart : await retrouver(champs("depart"));
      const arrivee = choix.arrivee && champs("arrivee").dataset.choisie ? choix.arrivee : await retrouver(champs("arrivee"));
      if (!depart || !arrivee) {
        resultat.innerHTML = '<p class="alerte">Commune introuvable : choisissez-la dans la liste proposée.</p>';
        return;
      }
      champs("depart").value = `${depart.nom} (${depart.departement})`;
      champs("arrivee").value = `${arrivee.nom} (${arrivee.departement})`;
      const route = await itineraire(depart, arrivee);
      afficher(depart, arrivee, route);
    } finally {
      bouton.disabled = false;
      bouton.textContent = "Calculer";
    }
  }

  function afficher(depart, arrivee, route) {
    const carburant = champs("carburant").value;
    const consoT = Number(champs("conso-thermique").value) || CONSO_DEFAUT[carburant];
    const consoE = Number(champs("conso-electrique").value) || 18;
    const batterie = Number(champs("batterie").value) || 60;
    const chargeDepart = Math.min(1, Math.max(RESERVE + 0.05, (Number(champs("charge-depart").value) || 90) / 100));
    const prixDomicile = Number(champs("prix-domicile").value) || resume.electricite.domicile?.prix || 0.25;
    const puissanceMin = Number(champs("reseau").value);

    const jalons = reperes(route);
    const km = route.km;
    const surRoute = leLongDe(stations.filter((s) => s[carburant] !== null), jalons);
    const bornes = leLongDe(sites.filter((b) => b.puissance_max_kw >= puissanceMin), jalons);

    // Thermique : au prix médian des stations du trajet, et à la moins chère
    const prix = surRoute.map((s) => s[carburant]).sort((a, b) => a - b);
    const prixMedian = prix.length ? prix[Math.floor((prix.length - 1) / 2)] : resume.carburants[carburant].mediane;
    const moinsChere = surRoute.length ? surRoute.reduce((a, b) => (b[carburant] < a[carburant] ? b : a)) : null;
    const litres = (km * consoT) / 100;
    const coutMedian = litres * prixMedian;
    const coutMoinsChere = moinsChere ? litres * moinsChere[carburant] : null;

    // Électrique : la batterie du départ (chargée à domicile) puis les recharges rapides
    const energie = (km * consoE) / 100;
    const { arrets, impossible } = recharges(bornes, km, consoE, batterie, chargeDepart);
    const energieRoute = arrets.reduce((s, a) => s + a.energie, 0);
    const coutRoute = arrets.reduce((s, a) => s + a.cout, 0);
    const coutElectrique = (energie - energieRoute) * prixDomicile + coutRoute;
    const minutesRecharge = arrets.reduce((s, a) => s + a.minutes, 0);

    const lignes = [
      { libelle: `${Commun.CARBURANTS[carburant]}, prix médian du trajet`, valeur: coutMedian, couleur: COULEURS.thermique },
    ];
    if (coutMoinsChere !== null) {
      lignes.push({ libelle: "Plein à la moins chère du trajet", valeur: coutMoinsChere, couleur: COULEURS.thermique, opacite: 0.6 });
    }
    if (!impossible) {
      lignes.push({ libelle: arrets.length ? "Électrique, avec les recharges" : "Électrique, chargée à domicile", valeur: coutElectrique, couleur: COULEURS.electrique });
    }
    if (arrets.length) {
      lignes.push({ libelle: "Électrique, tout au tarif domicile", valeur: energie * prixDomicile, couleur: COULEURS.electrique, opacite: 0.6 });
    }

    let html = `<p class="resume-trajet">${echapper(depart.nom)} → ${echapper(arrivee.nom)}</p>`;
    html += `<p class="discret petit" style="margin:0">${route.estime ? "Environ " : ""}${nombre(km)} km${route.duree ? ` · ${duree(route.duree)} de route` : ""}${route.estime ? " (itinéraire indisponible : estimation à vol d'oiseau, +25 %)" : ""}</p>`;
    html += `<div class="bloc"><h2>Coût de l'énergie</h2>${barres(lignes)}</div>`;

    // Détail thermique
    html += `<div class="bloc"><h2><i class="pastille" style="background:var(--orange)"></i>${Commun.CARBURANTS[carburant]} : ${nombre(litres, 1)} litres</h2>`;
    if (moinsChere) {
      html += `<p class="petit" style="margin:0">${surRoute.length} stations le long de la route, prix médian ${euros(prixMedian, 3)}. La moins chère : <b>${echapper(moinsChere.enseigne || "station")}</b> à ${echapper(moinsChere.ville)}, km ${nombre(moinsChere.km)}, à ${euros(moinsChere[carburant], 3)}${moinsChere.autoroute ? " (autoroute)" : ""}.</p>`;
      const gain = (prixMedian - moinsChere[carburant]) * 50;
      if (gain >= 0.5) html += `<p class="economie">Un plein de 50 litres y coûte ${euros(gain)} de moins qu'au prix médian du trajet.</p>`;
    } else {
      html += `<p class="petit discret" style="margin:0">Aucune station ne déclare de prix récent le long de la route ; coût calculé au prix médian national.</p>`;
    }
    html += "</div>";

    // Détail électrique
    html += `<div class="bloc"><h2><i class="pastille" style="background:var(--violet)"></i>Électrique : ${nombre(energie, 0)} kWh</h2>`;
    if (impossible && route.estime) {
      html += `<p class="alerte">L'itinéraire routier n'a pas pu être calculé : la ligne droite passe loin des bornes des grands axes, et le plan de recharge n'est pas fiable. Réessayez dans un instant.</p>`;
    } else if (impossible) {
      const conseils = [puissanceMin >= 150 ? "toutes les bornes rapides" : null, "une batterie plus grande", "une charge au départ plus élevée"].filter(Boolean);
      html += `<p class="alerte">Pas de borne ${puissanceMin >= 150 ? "ultra-rapide" : "rapide"} à portée sur une partie de la route avec cette autonomie : essayez ${conseils.slice(0, -1).join(", ")} ou ${conseils[conseils.length - 1]}.</p>`;
    } else if (!arrets.length) {
      html += `<p class="petit" style="margin:0">Pas besoin de recharger en route : la batterie chargée à ${nombre(chargeDepart * 100)} % suffit, au tarif domicile (${euros(prixDomicile, 3)} le kWh).</p>`;
    } else {
      html += `<p class="petit" style="margin:0 0 6px">${arrets.length} recharge${arrets.length > 1 ? "s" : ""} (environ ${duree(minutesRecharge * 60)} en tout), ${nombre(energieRoute, 0)} kWh payés sur la route ; le reste vient de la batterie chargée à domicile.</p><ol class="arrets">`;
      for (const a of arrets) {
        html += `<li><b>km ${nombre(a.borne.km)}</b> · ${echapper(a.borne.operateurs || "opérateur inconnu")}, ${nombre(a.borne.puissance_max_kw)} kW<br><span class="discret">de ${nombre(a.arrivee * 100)} à ${nombre(a.cible * 100)} % : ${nombre(a.energie, 0)} kWh à ${euros(a.prix, 2)}${a.declare ? "" : " (tarif médian)"}, ${euros(a.cout)}, environ ${nombre(a.minutes)} min</span></li>`;
      }
      html += "</ol>";
    }
    html += `<p class="tres-petit discret" style="margin:8px 0 0">${bornes.length} sites de recharge ${puissanceMin >= 150 ? "ultra-rapide" : "rapide"} à moins de 2 km de la route. Réserve de 10 % à chaque arrivée, recharge jusqu'à 80 %, puissance moyenne de la moitié de celle de la borne (75 kW au plus).</p></div>`;
    resultat.innerHTML = html;

    dessiner(depart, arrivee, route, surRoute, carburant, moinsChere, bornes, arrets);
  }

  function dessiner(depart, arrivee, route, surRoute, carburant, moinsChere, bornes, arrets) {
    calque.clearLayers();
    const ligne = L.polyline(route.points, { color: "#0b0b0b", weight: 4, opacity: 0.75, dashArray: route.estime ? "6 8" : null }).addTo(calque);
    for (const s of surRoute) {
      L.circleMarker([s.latitude, s.longitude], { radius: 4, color: "#fff", weight: 0.8, fillColor: COULEURS.thermique, fillOpacity: 0.55 })
        .bindTooltip(`${echapper(s.enseigne || "Station")} · ${euros(s[carburant], 3)}`)
        .addTo(calque);
    }
    for (const b of bornes) {
      L.circleMarker([b.latitude, b.longitude], { radius: 3.5, color: "#fff", weight: 0.8, fillColor: COULEURS.electrique, fillOpacity: 0.55 })
        .bindTooltip(`${echapper(b.operateurs || "Recharge rapide")} · ${nombre(b.puissance_max_kw)} kW`)
        .addTo(calque);
    }
    if (moinsChere) {
      L.circleMarker([moinsChere.latitude, moinsChere.longitude], { radius: 9, color: "#fff", weight: 2, fillColor: COULEURS.thermique, fillOpacity: 1 })
        .bindTooltip(`La moins chère : ${euros(moinsChere[carburant], 3)}`, { direction: "top", offset: [0, -8] })
        .addTo(calque);
    }
    arrets.forEach((a, i) => {
      L.marker([a.borne.latitude, a.borne.longitude], {
        icon: L.divIcon({ className: "", html: `<div class="icone-arret">${i + 1}</div>`, iconSize: [24, 24], iconAnchor: [12, 12] }),
      })
        .bindTooltip(`Recharge ${i + 1} : ${echapper(a.borne.operateurs || "")}, ${nombre(a.borne.puissance_max_kw)} kW`)
        .addTo(calque);
    });
    for (const c of [depart, arrivee]) {
      L.marker([c.latitude, c.longitude], { icon: L.divIcon({ className: "", html: '<div class="icone-etape"></div>', iconSize: [16, 16], iconAnchor: [8, 8] }) })
        .bindTooltip(echapper(c.nom))
        .addTo(calque);
    }
    carte.fitBounds(ligne.getBounds(), { padding: [30, 30] });
  }

  champs("formulaire").addEventListener("submit", (e) => {
    e.preventDefault();
    calculer();
  });
  calculer();
})();
