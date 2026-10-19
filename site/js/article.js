// Le bandeau « Aujourd'hui » de l'article : les prix et les coûts du jour, tirés de donnees/resume.json.

(async function () {
  const { euros, date } = Commun;
  const ecrire = (cle, texte) => {
    const element = document.querySelector(`[data-chiffre="${cle}"]`);
    if (element && texte) element.textContent = texte;
  };
  let resume;
  try {
    resume = await Commun.charger("resume");
  } catch {
    document.getElementById("date-du-jour").textContent = "données du jour indisponibles";
    return;
  }
  document.getElementById("date-du-jour").textContent =
    `prix du ${date(resume.date_prix)}, ${resume.stations.toLocaleString("fr-FR")} stations`;
  const { Gazole: gazole, E10: e10 } = resume.carburants;
  if (gazole) {
    ecrire("gazole", euros(gazole.mediane, 3));
    ecrire("gazole-plage", `${euros(gazole.p10)} à ${euros(gazole.p90)} pour 8 stations sur 10`);
  }
  if (e10) {
    ecrire("e10", euros(e10.mediane, 3));
    ecrire("e10-plage", `${euros(e10.p10)} à ${euros(e10.p90)} pour 8 stations sur 10`);
  }
  const cout = resume.cout_100km;
  ecrire("cout-gazole", euros(cout.Gazole));
  ecrire("cout-domicile", euros(cout.domicile));
  ecrire("cout-ultra", euros(cout.ultra));
  const kwh = resume.electricite;
  if (kwh.domicile) ecrire("domicile-prix", `18 kWh à ${euros(kwh.domicile.prix, 3)}`);
  if (kwh.ultra) ecrire("ultra-prix", `18 kWh à ${euros(kwh.ultra.prix, 2)} (tarif médian)`);
})();
