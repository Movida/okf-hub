// okf-hub — quelques améliorations, et la page s'en passe : sans ce script, tout
// se lit et se navigue de la même façon. Il ne lit que des pages que le hub sert
// déjà (la liste de /aller), et n'envoie rien.
//
// - Ctrl+K (⌘K) ouvre la palette : une fenêtre qui affiche, pendant la frappe,
//   la liste que sert /aller ; sans script, le champ de l'en-tête mène à la même
//   page.
// - J et K suivent les liens rel="next" et rel="prev" de la page (la revue des
//   propositions, une proposition après l'autre).
// - Un bouton « copier » met sa commande dans le presse-papiers.
// - Un formulaire de filtres marqué « auto » s'applique dès qu'on change un choix.
// - Le sommaire d'un document suit la section qu'on lit.
// - Sur la palette et la page du saut, ↑ et ↓ passent d'un résultat à l'autre.
// - Un champ qui n'a de sens qu'avec un autre (data-suppose) reste grisé tant
//   que l'autre est vide.

const dansUnChamp = (cible) => cible.closest("input, textarea, select, [contenteditable]");

// --- palette (Ctrl+K) ----------------------------------------------------------
// Elle lit la page /aller?liste=1 — celle que le hub sert à qui l'ouvre — et en
// insère la liste : aucune donnée qui ne soit déjà une page.
const palette = document.getElementById("palette");
const champPalette = palette && palette.querySelector("input[name=q]");
const resultats = palette && palette.querySelector("#palette-resultats");
let demande = 0;
let attente = null;

const liensDe = (racine) => [...racine.querySelectorAll(".palette-groupes a")];

function choisir(racine, lien) {
  for (const l of liensDe(racine)) l.removeAttribute("aria-selected");
  if (lien) {
    lien.setAttribute("aria-selected", "true");
    lien.scrollIntoView({ block: "nearest" });
  }
}

async function remplir(q) {
  const numero = ++demande;
  const reponse = await fetch(`/aller?${new URLSearchParams({ q, liste: "1" })}`, { headers: { Accept: "text/html" } });
  if (numero !== demande || !reponse.ok) return;
  const page = new DOMParser().parseFromString(await reponse.text(), "text/html");
  const groupes = page.querySelector(".palette-groupes");
  resultats.replaceChildren(...(groupes ? [document.importNode(groupes, true)] : []));
  choisir(resultats, liensDe(resultats)[0]);
}

function ouvrirPalette(texte) {
  if (!palette || typeof palette.showModal !== "function") return false;
  if (!palette.open) palette.showModal();
  champPalette.value = texte || "";
  champPalette.focus();
  champPalette.select();
  remplir(champPalette.value);
  return true;
}

if (palette) {
  champPalette.addEventListener("input", () => {
    clearTimeout(attente);
    attente = setTimeout(() => remplir(champPalette.value.trim()), 120);
  });
  palette.addEventListener("keydown", (evenement) => {
    const liens = liensDe(resultats);
    const ici = liens.findIndex((l) => l.getAttribute("aria-selected") === "true");
    if (evenement.key === "ArrowDown" || evenement.key === "ArrowUp") {
      evenement.preventDefault();
      const pas = evenement.key === "ArrowDown" ? 1 : -1;
      choisir(resultats, liens[Math.min(liens.length - 1, Math.max(0, ici + pas))]);
    } else if (evenement.key === "Enter" && ici >= 0) {
      evenement.preventDefault();
      location.href = liens[ici].href;
    }
  });
  // Un clic hors de la fenêtre la ferme.
  palette.addEventListener("click", (evenement) => { if (evenement.target === palette) palette.close(); });
  const saut = document.getElementById("saut");
  if (saut) saut.addEventListener("focus", () => { if (ouvrirPalette(saut.value)) saut.blur(); });
}

document.addEventListener("keydown", (evenement) => {
  if ((evenement.ctrlKey || evenement.metaKey) && evenement.key.toLowerCase() === "k") {
    evenement.preventDefault();
    if (!ouvrirPalette("")) {
      const champ = document.getElementById("saut");
      if (champ) { champ.focus(); champ.select(); }
    }
    return;
  }
  if (evenement.ctrlKey || evenement.metaKey || evenement.altKey || dansUnChamp(evenement.target)) return;
  if (palette && palette.open) return;
  const rel = { j: "next", k: "prev" }[evenement.key];
  const lien = rel && document.querySelector(`a[rel="${rel}"]`);
  if (lien) location.href = lien.href;
});

for (const bouton of document.querySelectorAll("button.copier[data-copier]")) {
  if (!navigator.clipboard) continue;
  bouton.hidden = false;
  bouton.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(bouton.dataset.copier);
      bouton.textContent = "copié";
    } catch {
      bouton.textContent = "copie refusée";
    }
    setTimeout(() => { bouton.textContent = "copier"; }, 1500);
  });
}

for (const formulaire of document.querySelectorAll("form.auto")) {
  formulaire.addEventListener("change", () => formulaire.requestSubmit());
  for (const bouton of formulaire.querySelectorAll("button[type=submit]")) bouton.hidden = true;
}

const sommaire = document.querySelector("nav.toc");
if (sommaire && "IntersectionObserver" in window) {
  const liens = new Map();
  for (const lien of sommaire.querySelectorAll('a[href^="#"]')) {
    const titre = document.getElementById(decodeURIComponent(lien.hash.slice(1)));
    if (titre) liens.set(titre, lien);
  }
  let courant = null;
  const observateur = new IntersectionObserver((entrees) => {
    for (const entree of entrees) {
      if (!entree.isIntersecting) continue;
      if (courant) courant.removeAttribute("aria-current");
      courant = liens.get(entree.target);
      courant.setAttribute("aria-current", "location");
    }
  }, { rootMargin: "0px 0px -75% 0px" });
  for (const titre of liens.keys()) observateur.observe(titre);
}

// Sur la page /aller elle-même, ↑ et ↓ passent d'un résultat à l'autre.
const groupesDeLaPage = document.querySelector("main .palette-groupes");
if (groupesDeLaPage) {
  const liens = liensDe(document.querySelector("main"));
  document.addEventListener("keydown", (evenement) => {
    if (palette && palette.open) return;
    if (evenement.key !== "ArrowDown" && evenement.key !== "ArrowUp") return;
    const ici = liens.indexOf(document.activeElement);
    const suivant = liens[Math.min(liens.length - 1, Math.max(0, ici + (evenement.key === "ArrowDown" ? 1 : -1)))];
    if (suivant) {
      evenement.preventDefault();
      suivant.focus();
    }
  });
}

for (const dependant of document.querySelectorAll("[data-suppose]")) {
  const maitre = dependant.form && dependant.form.elements[dependant.dataset.suppose];
  if (!maitre) continue;
  const accorder = () => { dependant.disabled = !maitre.value.trim(); };
  maitre.addEventListener("input", accorder);
  accorder();
}
