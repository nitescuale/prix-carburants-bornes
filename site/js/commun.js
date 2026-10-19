// Outils partagés par la carte et le trajet : chargement des données, formats français, distances, autocomplétion.

const Commun = (() => {
  const cache = {};

  /** Charge un fichier de données du site (donnees/<nom>.json), une seule fois. */
  function charger(nom) {
    if (!cache[nom]) {
      cache[nom] = fetch(`donnees/${nom}.json`).then((reponse) => {
        if (!reponse.ok) throw new Error(`${nom}.json : ${reponse.status}`);
        return reponse.json();
      });
    }
    return cache[nom];
  }

  /** {colonnes, lignes} → liste d'objets. */
  function objets(table) {
    const { colonnes, lignes } = table;
    return lignes.map((ligne) => Object.fromEntries(colonnes.map((c, i) => [c, ligne[i]])));
  }

  const formats = {};
  function nombre(valeur, decimales = 0) {
    if (valeur === null || valeur === undefined || Number.isNaN(valeur)) return "–";
    formats[decimales] ??= new Intl.NumberFormat("fr-FR", {
      minimumFractionDigits: decimales,
      maximumFractionDigits: decimales,
    });
    return formats[decimales].format(valeur).replace("-", "−");
  }
  const euros = (valeur, decimales = 2) => `${nombre(valeur, decimales)} €`;
  const signe = (valeur, decimales = 1) => (valeur > 0 ? "+" : "") + nombre(valeur, decimales);

  function date(iso, options = { day: "numeric", month: "long", year: "numeric" }) {
    if (!iso) return "–";
    return new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", options);
  }

  /** Distance à vol d'oiseau en km. */
  function distanceKm(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad;
    const dLon = (lon2 - lon1) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371 * Math.asin(Math.sqrt(a));
  }

  /** Minuscules sans accents ni ponctuation, pour comparer des noms de communes. */
  function normaliser(texte) {
    return texte
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[-'’]/g, " ")
      .replace(/\bst\b/g, "saint")
      .replace(/\bste\b/g, "sainte")
      .replace(/\s+/g, " ")
      .trim();
  }

  let communesPromesse = null;
  /** Les communes de métropole, les plus peuplées d'abord, avec leur nom normalisé. */
  function communes() {
    communesPromesse ??= charger("communes").then((table) =>
      objets(table).map((c) => ({ ...c, cle: normaliser(c.nom) })),
    );
    return communesPromesse;
  }

  /** Autocomplétion d'une commune sur un champ texte ; `choisir` reçoit la commune retenue. */
  function autocompletion(champ, choisir) {
    const conteneur = document.createElement("div");
    conteneur.className = "autocomplete";
    champ.parentNode.insertBefore(conteneur, champ);
    conteneur.appendChild(champ);
    const liste = document.createElement("ul");
    liste.setAttribute("role", "listbox");
    liste.hidden = true;
    conteneur.appendChild(liste);
    champ.setAttribute("autocomplete", "off");
    champ.setAttribute("role", "combobox");
    champ.setAttribute("aria-expanded", "false");

    let propositions = [];
    let active = -1;

    function fermer() {
      liste.hidden = true;
      champ.setAttribute("aria-expanded", "false");
      active = -1;
    }

    function retenir(commune) {
      champ.value = `${commune.nom} (${commune.departement})`;
      champ.dataset.choisie = "1";
      fermer();
      choisir(commune);
    }

    function afficher() {
      liste.innerHTML = "";
      propositions.forEach((c, i) => {
        const li = document.createElement("li");
        li.setAttribute("role", "option");
        li.setAttribute("aria-selected", i === active ? "true" : "false");
        li.innerHTML = `<span>${echapper(c.nom)}</span><small>${echapper(c.departement)}</small>`;
        li.addEventListener("mousedown", (e) => {
          e.preventDefault();
          retenir(c);
        });
        liste.appendChild(li);
      });
      liste.hidden = propositions.length === 0;
      champ.setAttribute("aria-expanded", String(!liste.hidden));
    }

    champ.addEventListener("focus", () => communes(), { once: true });
    champ.addEventListener("input", async () => {
      champ.dataset.choisie = "";
      const requete = normaliser(champ.value.replace(/\(.*\)/, ""));
      if (requete.length < 2) {
        propositions = [];
        afficher();
        return;
      }
      const toutes = await communes();
      const debut = [];
      const ailleurs = [];
      for (const c of toutes) {
        if (c.cle.startsWith(requete)) debut.push(c);
        else if (debut.length < 8 && c.cle.includes(` ${requete}`)) ailleurs.push(c);
        if (debut.length >= 8) break;
      }
      propositions = [...debut, ...ailleurs].slice(0, 8);
      active = propositions.length ? 0 : -1;
      afficher();
    });
    champ.addEventListener("keydown", (e) => {
      if (liste.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const pas = e.key === "ArrowDown" ? 1 : -1;
        active = (active + pas + propositions.length) % propositions.length;
        afficher();
      } else if (e.key === "Enter" && active >= 0) {
        e.preventDefault();
        retenir(propositions[active]);
      } else if (e.key === "Escape") {
        fermer();
      }
    });
    champ.addEventListener("blur", () => setTimeout(fermer, 100));
  }

  function echapper(texte) {
    return String(texte ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  // Les carburants du flux officiel et leur libellé
  const CARBURANTS = {
    Gazole: "Gazole",
    E10: "SP95-E10",
    SP95: "SP95",
    SP98: "SP98",
    E85: "E85 (superéthanol)",
    GPLc: "GPL",
  };

  // Fond de carte : CARTO Positron, sobre, qui laisse lire les points de couleur
  function fondDeCarte(carte) {
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(carte);
  }

  return { charger, objets, nombre, euros, signe, date, distanceKm, normaliser, communes, autocompletion, echapper, CARBURANTS, fondDeCarte };
})();
