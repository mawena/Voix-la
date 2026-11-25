// Envoi des vidéos et enregistrements par morceaux de 8 Mo : barre d'avancement en temps réel,
// reprise automatique après une coupure réseau, annulation. Sans JavaScript, le formulaire
// classique (un seul envoi) reste utilisé.
(function () {
  'use strict';
  if (!window.fetch || !window.Blob || !Blob.prototype.slice) return;

  function lisible(o) {
    if (o < 1024 * 1024) return Math.round(o / 1024) + ' Ko';
    if (o < 1024 * 1024 * 1024) return (o / 1024 / 1024).toFixed(1).replace('.', ',') + ' Mo';
    return (o / 1024 / 1024 / 1024).toFixed(2).replace('.', ',') + ' Go';
  }
  function duree(s) {
    if (!isFinite(s)) return '…';
    s = Math.max(1, Math.round(s));
    return s < 60 ? s + ' s' : Math.floor(s / 60) + ' min ' + String(s % 60).padStart(2, '0');
  }
  const pause = ms => new Promise(r => setTimeout(r, ms));

  document.querySelectorAll('form[data-envoi]').forEach(function (form) {
    const sorte = form.dataset.envoi;
    const champ = form.querySelector('input[type=file]');
    const bouton = form.querySelector('button.plein');
    const boite = form.querySelector('.envoi');
    const barre = boite.querySelector('progress');
    const pourcent = boite.querySelector('.pourcent');
    const detail = boite.querySelector('.detail');
    const erreur = form.querySelector('.erreur-envoi');
    const csrf = form.querySelector('input[name=csrf]').value;
    let arret = null;   // AbortController de l'envoi en cours

    function entetes(plus) { return Object.assign({'X-CSRF': csrf}, plus || {}); }
    async function json(r) { try { return await r.json(); } catch (_) { return {}; } }

    function etat(actif) {
      window.envoiEnCours = actif;
      boite.hidden = !actif;
      bouton.disabled = actif;
      form.querySelectorAll('input, select').forEach(e => { e.disabled = actif; });
    }
    function echec(texte) {
      etat(false);
      erreur.textContent = texte;
      erreur.hidden = false;
    }

    boite.querySelector('.annuler-envoi').addEventListener('click', function () {
      if (arret) arret.abort();
    });

    form.addEventListener('submit', async function (ev) {
      const fichier = champ.files[0];
      if (!fichier) return;
      ev.preventDefault();
      erreur.hidden = true;
      const demande = {sorte: sorte, nom_fichier: fichier.name, taille: fichier.size};
      new FormData(form).forEach((v, k) => { if (k !== 'csrf' && typeof v === 'string') demande[k] = v; });
      etat(true);
      barre.value = 0; pourcent.textContent = '0 %'; detail.textContent = 'préparation…';
      arret = new AbortController();
      const signal = arret.signal;
      let jeton = null;
      try {
        let r = await fetch('/envois', {method: 'POST', credentials: 'same-origin', signal: signal,
          headers: entetes({'Content-Type': 'application/json'}), body: JSON.stringify(demande)});
        let j = await json(r);
        if (!r.ok) return echec(j.erreur || 'Envoi refusé (' + r.status + ').');
        jeton = j.jeton;
        const taille = j.morceau;
        let recu = 0, essais = 0;
        const t0 = performance.now();
        while (recu < fichier.size) {
          const morceau = fichier.slice(recu, Math.min(recu + taille, fichier.size));
          try {
            r = await fetch('/envois/' + jeton, {method: 'PUT', credentials: 'same-origin', signal: signal,
              headers: entetes({'X-Debut': String(recu), 'Content-Type': 'application/octet-stream'}), body: morceau});
            j = await json(r);
            if (r.ok || r.status === 409) {   // 409 : le serveur donne la vraie position
              recu = j.recu; essais = 0;
            } else if (r.status >= 500 || r.status === 429) {
              throw new Error('serveur ' + r.status);
            } else {
              return echec(j.erreur || 'Envoi refusé (' + r.status + ').');
            }
          } catch (e) {
            if (signal.aborted) throw e;
            if (++essais > 8) return echec('Connexion perdue : envoi interrompu. Réessayez.');
            detail.textContent = 'connexion perdue, nouvel essai (' + essais + '/8)…';
            await pause(Math.min(30000, 1000 * 2 ** essais));
            try {   // reprendre là où le serveur s'est arrêté
              const e2 = await fetch('/envois/' + jeton, {credentials: 'same-origin', signal: signal, headers: entetes()});
              if (e2.ok) recu = (await e2.json()).recu;
            } catch (_) { if (signal.aborted) throw _; }
            continue;
          }
          const p = recu / fichier.size;
          const ecoule = (performance.now() - t0) / 1000;
          const debit = recu / Math.max(ecoule, 0.001);
          barre.value = p * 100;
          pourcent.textContent = Math.floor(p * 100) + ' %';
          detail.textContent = lisible(recu) + ' sur ' + lisible(fichier.size) + ' · ' + lisible(debit) + '/s · reste '
            + duree((fichier.size - recu) / debit);
        }
        detail.textContent = sorte === 'doublage' ? 'vérification de la vidéo…' : 'enregistrement…';
        barre.removeAttribute('value');   // barre indéterminée pendant la vérification
        r = await fetch('/envois/' + jeton + '/fin', {method: 'POST', credentials: 'same-origin', headers: entetes()});
        j = await json(r);
        if (!r.ok) return echec(j.erreur || 'Envoi non terminé (' + r.status + ').');
        window.envoiEnCours = false;
        location.href = j.aller || '/tableau';
      } catch (e) {
        if (jeton) fetch('/envois/' + jeton, {method: 'DELETE', credentials: 'same-origin', headers: entetes()}).catch(() => {});
        echec(signal.aborted ? 'Envoi annulé.' : 'Envoi impossible : ' + e.message);
      } finally {
        arret = null;
      }
    });
  });

  window.addEventListener('beforeunload', function (ev) {
    if (window.envoiEnCours) { ev.preventDefault(); ev.returnValue = ''; }
  });
})();
