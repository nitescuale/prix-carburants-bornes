// La carte des prix : une pastille par station, colorée selon son prix du jour, et les sites de recharge rapide.

(async function () {
  const { nombre, euros, signe, echapper, distanceKm } = Commun;
  const COULEURS = ["#1baf7a", "#8cc97f", "#e3c45a", "#f0954a", "#d9492a"];
  const NOMS_CLASSES = ["les 20 % les moins chers", "", "", "", "les 20 % les plus chers"];
  const GROUPES = {
    enseigne: "Enseigne",
    autoroute: "Autoroute",
    lieu: "Lieu",
    equipement: "Équipement",
    anciennete: "Ancienneté",
  };

  const carte = L.map("carte", { preferCanvas: true, zoomSnap: 0.5 }).setView([46.6, 2.5], 6);
  Commun.fondDeCarte(carte);
  // Les bornes sous les stations : deux plans, chacun avec son canevas
  carte.createPane("bornes").style.zIndex = 390;
  carte.createPane("stations").style.zIndex = 400;
  const rendusBornes = L.canvas({ pane: "bornes" });
  const rendusStations = L.canvas({ pane: "stations" });
  const calqueStations = L.layerGroup().addTo(carte);
  const calqueBornes = L.layerGroup().addTo(carte);
  const calquePoint = L.layerGroup().addTo(carte);

  const choixCarburant = document.getElementById("carburant");
  const choixRayon = document.getElementById("rayon");
  const caseBornes = document.getElementById("bornes");
  const panneauAutour = document.getElementById("autour");

  let resume, stations, sites;
  try {
    const [r, s, b] = await Promise.all([Commun.charger("resume"), Commun.charger("stations"), Commun.charger("recharge")]);
    resume = r;
    stations = Commun.objets(s);
    sites = Commun.objets(b);
  } catch (erreur) {
    document.getElementById("sous-titre").textContent = `Les données n'ont pas pu être chargées (${erreur.message}).`;
    return;
  }

  // La décomposition du modèle n'est pas indispensable : la carte s'affiche sans elle
  let explications = null;
  let meta = null;
  Commun.charger("explication")
    .then((table) => {
      meta = table;
      explications = new Map(Commun.objets(table).map((e) => [e.station_id, e]));
    })
    .catch(() => {});

  document.getElementById("sous-titre").innerHTML =
    `Prix du ${Commun.date(resume.date_prix)} dans ${nombre(stations.length)} stations de métropole, ` +
    `et ${nombre(sites.length)} sites de recharge rapide.`;

  for (const [code, libelle] of Object.entries(Commun.CARBURANTS)) {
    if (!resume.carburants[code]) continue;
    const option = new Option(`${libelle} · ${euros(resume.carburants[code].mediane, 3)}`, code);
    choixCarburant.add(option);
  }

  let carburant = "Gazole";
  let seuils = [];
  let mediane = null;

  function quantile(tries, q) {
    return tries[Math.round(q * (tries.length - 1))];
  }

  function couleur(prix) {
    let i = 0;
    while (i < seuils.length && prix > seuils[i]) i++;
    return COULEURS[i];
  }

  function rayonPastille() {
    const z = carte.getZoom();
    return z <= 6 ? 2.6 : z <= 8 ? 3.4 : z <= 11 ? 5 : 7;
  }

  function rayonBorne() {
    const z = carte.getZoom();
    return z <= 7 ? 1.8 : rayonPastille() - 0.8;
  }

  function nomStation(s) {
    return s.enseigne || "Station";
  }

  function legende() {
    const morceaux = COULEURS.map((c, i) => {
      const bas = i === 0 ? null : seuils[i - 1];
      const haut = i === seuils.length ? null : seuils[i];
      const texte = bas === null ? `jusqu'à ${euros(haut, 3)}` : haut === null ? `plus de ${euros(bas, 3)}` : `${euros(bas, 3)} à ${euros(haut, 3)}`;
      return `<span title="${NOMS_CLASSES[i]}"><i style="background:${c}"></i>${texte}</span>`;
    });
    document.getElementById("legende").innerHTML = morceaux.join("");
  }

  function dessinerStations() {
    calqueStations.clearLayers();
    const prix = stations.map((s) => s[carburant]).filter((p) => p !== null).sort((a, b) => a - b);
    seuils = [0.2, 0.4, 0.6, 0.8].map((q) => quantile(prix, q));
    mediane = quantile(prix, 0.5);
    legende();
    const r = rayonPastille();
    for (const s of stations) {
      const p = s[carburant];
      if (p === null) continue;
      const marque = L.circleMarker([s.latitude, s.longitude], {
        renderer: rendusStations,
        pane: "stations",
        radius: r,
        color: "#ffffff",
        weight: 0.6,
        fillColor: couleur(p),
        fillOpacity: 0.95,
      });
      marque.bindPopup(() => popupStation(s), { maxWidth: 320, maxHeight: 440 });
      marque.station = s;
      calqueStations.addLayer(marque);
    }
  }

  function dessinerBornes() {
    calqueBornes.clearLayers();
    if (!caseBornes.checked) return;
    const r = rayonBorne();
    for (const site of sites) {
      const marque = L.circleMarker([site.latitude, site.longitude], {
        renderer: rendusBornes,
        pane: "bornes",
        radius: r,
        color: "#ffffff",
        weight: 1,
        fillColor: "#8a5cd6",
        fillOpacity: 0.9,
      });
      marque.bindPopup(() => popupBorne(site), { maxWidth: 300 });
      calqueBornes.addLayer(marque);
    }
  }

  function popupStation(s) {
    const lignes = Object.entries(Commun.CARBURANTS)
      .filter(([code]) => s[code] !== null)
      .map(([code, libelle]) => {
        const med = resume.carburants[code]?.mediane;
        const ecart = med ? (s[code] - med) * 100 : null;
        const texteEcart = ecart === null ? "" : `${signe(ecart, 1)} c vs médiane`;
        return `<tr class="${code === carburant ? "choisi" : ""}"><td>${libelle}</td><td class="chiffre"><b>${euros(s[code], 3)}</b></td><td>${texteEcart}</td></tr>`;
      })
      .join("");
    const badges = [
      s.autoroute ? '<span class="badge autoroute">Autoroute</span>' : "",
      s.automate ? '<span class="badge">24 h/24</span>' : "",
    ].join(" ");
    return `<div class="popup">
      <h3>${echapper(nomStation(s))}</h3>
      <div class="adresse">${echapper(s.adresse)}, ${echapper(s.cp)} ${echapper(s.ville)} ${badges}</div>
      <table>${lignes}</table>
      <div class="tres-petit discret" style="margin-top:6px">Mis à jour le ${Commun.date(s.maj)}</div>
      ${pourquoi(s)}
    </div>`;
  }

  /** La décomposition du modèle de prix (gazole, 2025) pour une station, s'il l'a vue cette année-là. */
  function pourquoi(s) {
    if (!explications) return "";
    const e = explications.get(s.station_id);
    if (!e) {
      return `<div class="pourquoi tres-petit discret">Station absente du modèle de prix (moins de 26 semaines de prix du gazole en ${meta.annee}, ou Corse).</div>`;
    }
    const echelle = Math.max(4, ...Object.keys(GROUPES).map((cle) => Math.abs(e[cle])));
    const barres = Object.entries(GROUPES)
      .map(([cle, libelle]) => {
        const v = e[cle];
        const largeur = Math.min(Math.abs(v), echelle) / echelle * 50;
        const gauche = v >= 0 ? 50 : 50 - largeur;
        const teinte = v >= 0 ? "var(--orange)" : "var(--aqua)";
        return `<div class="barre"><span>${libelle}</span><span class="piste"><span class="remplissage" style="left:${gauche}%;width:${largeur}%;background:${teinte}"></span></span><span class="valeur">${signe(v, 1)} c</span></div>`;
      })
      .join("");
    const sens = e.reel >= 0 ? "au-dessus du" : "sous le";
    return `<div class="pourquoi">
      <div class="etiquette">Pourquoi ce prix ?</div>
      <p style="margin:4px 0 8px">En ${meta.annee}, le gazole y était en moyenne <b>${nombre(Math.abs(e.reel), 1)} centimes ${sens}</b> prix médian national. Le modèle, qui n'avait pas vu cette station, prévoyait ${signe(e.prediction, 1)} c :</p>
      <div class="barres centrees">${barres}</div>
      <p class="tres-petit discret" style="margin:8px 0 0">Station moyenne : ${signe(e.base, 1)} c ; chaque barre ajoute ou retire des centimes à cette moyenne (valeurs de Shapley). Le lieu regroupe la position et la concurrence, l'équipement les services et les carburants vendus. Erreur moyenne du modèle : ${nombre(meta.erreur_moyenne, 1)} c.</p>
    </div>`;
  }

  function prixReference(site) {
    if (site.prix_kwh !== null) return { prix: site.prix_kwh, source: "tarif déclaré" };
    const classe = site.puissance_max_kw >= 150 ? "ultra" : "rapide";
    const t = resume.electricite[classe];
    return t ? { prix: t.prix, source: "tarif médian de sa puissance" } : null;
  }

  function popupBorne(site) {
    const ref = prixReference(site);
    const conso = resume.conso.electrique;
    const gazole = resume.cout_100km.Gazole;
    const cout = ref
      ? `<p style="margin:8px 0 0">À ${euros(ref.prix, 2)} le kWh (${ref.source}), 100 km coûtent <b>${euros(ref.prix * conso)}</b>, contre ${euros(gazole)} au gazole médian du jour.</p>`
      : "";
    return `<div class="popup">
      <h3>Recharge rapide <span class="badge rapide">${nombre(site.puissance_max_kw)} kW</span></h3>
      <div class="adresse">${echapper(site.operateurs || "Opérateur inconnu")}</div>
      <div>${nombre(site.points_de_charge)} point${site.points_de_charge > 1 ? "s" : ""} de charge, jusqu'à ${nombre(site.puissance_max_kw)} kW</div>
      ${cout}
      <p class="tres-petit discret" style="margin:6px 0 0">Coût de l'énergie seule, pour ${nombre(conso)} kWh aux 100 km, hors abonnement et frais de stationnement.</p>
    </div>`;
  }

  /** Les stations les moins chères autour d'un point, et la recharge rapide la plus proche. */
  function autour(lat, lon, nom) {
    const rayon = Number(choixRayon.value);
    calquePoint.clearLayers();
    L.circle([lat, lon], { radius: rayon * 1000, color: "#0b0b0b", weight: 1, dashArray: "4 4", fill: false, interactive: false }).addTo(calquePoint);
    L.circleMarker([lat, lon], { radius: 6, color: "#ffffff", weight: 2, fillColor: "#0b0b0b", fillOpacity: 1, interactive: false }).addTo(calquePoint);

    const proches = stations
      .filter((s) => s[carburant] !== null)
      .map((s) => ({ s, d: distanceKm(lat, lon, s.latitude, s.longitude) }))
      .filter((x) => x.d <= rayon)
      .sort((a, b) => a.s[carburant] - b.s[carburant] || a.d - b.d);
    const libelle = Commun.CARBURANTS[carburant];
    let html = `<h2>${nom ? `Autour de ${echapper(nom)}` : "Autour de ce point"} · ${libelle}</h2>`;
    if (proches.length === 0) {
      html += `<p class="petit discret">Aucune station ne déclare de prix récent pour ce carburant à moins de ${rayon} km.</p>`;
    } else {
      const tries = proches.map((x) => x.s[carburant]).sort((a, b) => a - b);
      const medLocale = quantile(tries, 0.5);
      html += `<p class="petit discret" style="margin:0">${proches.length} station${proches.length > 1 ? "s" : ""} à moins de ${rayon} km ; prix médian ${euros(medLocale, 3)} (France : ${euros(mediane, 3)}).</p>`;
      html += '<ol class="liste-stations">';
      for (const { s, d } of proches.slice(0, 5)) {
        html += `<li data-id="${s.station_id}"><span class="nom">${echapper(nomStation(s))}</span><span class="prix">${euros(s[carburant], 3)}</span><span class="detail">${echapper(s.adresse)}, ${echapper(s.ville)} · ${nombre(d, 1)} km${s.autoroute ? " · autoroute" : ""}</span></li>`;
      }
      html += "</ol>";
      const gain = (medLocale - tries[0]) * 50;
      if (gain >= 0.5) {
        html += `<p class="economie">Sur un plein de 50 litres, la moins chère fait économiser <b>${euros(gain)}</b> par rapport au prix médian du secteur.</p>`;
      }
    }
    const borne = sites
      .map((site) => ({ site, d: distanceKm(lat, lon, site.latitude, site.longitude) }))
      .sort((a, b) => a.d - b.d)[0];
    if (borne) {
      html += `<p class="petit" style="margin:12px 0 0"><i class="point-violet"></i> Recharge rapide la plus proche à <b>${nombre(borne.d, 1)} km</b> : ${echapper(borne.site.operateurs || "opérateur inconnu")}, jusqu'à ${nombre(borne.site.puissance_max_kw)} kW.</p>`;
    }
    panneauAutour.innerHTML = html;
    panneauAutour.querySelectorAll("li[data-id]").forEach((li) => {
      li.addEventListener("click", () => ouvrirStation(Number(li.dataset.id)));
    });
    dernierPoint = { lat, lon, nom };
  }

  function ouvrirStation(id) {
    calqueStations.eachLayer((marque) => {
      if (marque.station?.station_id === id) {
        carte.setView(marque.getLatLng(), Math.max(carte.getZoom(), 13));
        marque.openPopup();
      }
    });
  }

  let dernierPoint = null;

  carte.on("click", (e) => autour(e.latlng.lat, e.latlng.lng));
  carte.on("zoomend", () => {
    const r = rayonPastille();
    calqueStations.eachLayer((m) => m.setRadius(r));
    const rb = rayonBorne();
    calqueBornes.eachLayer((m) => m.setRadius(rb));
  });
  choixCarburant.addEventListener("change", () => {
    carburant = choixCarburant.value;
    dessinerStations();
    if (dernierPoint) autour(dernierPoint.lat, dernierPoint.lon, dernierPoint.nom);
  });
  choixRayon.addEventListener("change", () => {
    if (dernierPoint) autour(dernierPoint.lat, dernierPoint.lon, dernierPoint.nom);
  });
  caseBornes.addEventListener("change", dessinerBornes);

  Commun.autocompletion(document.getElementById("recherche"), (commune) => {
    carte.setView([commune.latitude, commune.longitude], 12);
    autour(commune.latitude, commune.longitude, commune.nom);
  });

  const boutonLocaliser = document.getElementById("localiser");
  if (!("geolocation" in navigator)) boutonLocaliser.hidden = true;
  boutonLocaliser.addEventListener("click", () => {
    boutonLocaliser.disabled = true;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        boutonLocaliser.disabled = false;
        const { latitude, longitude } = position.coords;
        carte.setView([latitude, longitude], 12);
        autour(latitude, longitude, "vous");
      },
      () => {
        boutonLocaliser.disabled = false;
        panneauAutour.innerHTML = '<p class="petit discret" style="margin:0">Position indisponible : cherchez plutôt une commune.</p>';
      },
      { timeout: 10000 },
    );
  });

  dessinerStations();
  dessinerBornes();
})();
