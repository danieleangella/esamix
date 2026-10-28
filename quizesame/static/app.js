// Script comuni a tutte le pagine, caricati una volta sola (e tenuti in cache dal browser).

// Collega i comportamenti dei moduli esercizio (_esercizio_form.html) presenti dentro
// `radice`: aggiunta/rimozione di varianti e risposte, domanda aperta, validazione prima
// dell'invio. Va richiamata anche dopo aver inserito via fetch un modulo nella pagina.
function inizializzaFormEsercizi(radice) {
  var forms = (radice || document).querySelectorAll(".form-esercizio");
  forms.forEach(function (form) {
    // può essere richiamata più volte sulla stessa pagina (un modulo per ogni inclusione,
    // più quelli caricati via fetch): senza questa guardia riattaccherebbe gli stessi
    // listener più volte (click che aggiungono più varianti alla volta).
    if (form.dataset.iniz) return;
    form.dataset.iniz = "1";
    var container = form.querySelector(".varianti-container");
    var tplVariante = form.querySelector(".tpl-variante");
    var tplSbagliata = form.querySelector(".tpl-sbagliata");
    var blocchiEsistenti = container.querySelectorAll(".variante-block");
    var nextVidx = 0;
    blocchiEsistenti.forEach(function (node) {
      var v = parseInt(node.querySelector(".campo-vidx").value, 10);
      if (!isNaN(v) && v >= nextVidx) nextVidx = v + 1;
    });

    function collegaSbagliata(node) {
      node.querySelector(".btn-rimuovi-sbagliata").addEventListener("click", function () {
        node.remove();
      });
    }

    function aggiungiSbagliata(sbagliateContainer, vidx) {
      var node = tplSbagliata.content.firstElementChild.cloneNode(true);
      node.querySelector(".campo-sbagliata").name = "sbagliata_" + vidx;
      collegaSbagliata(node);
      sbagliateContainer.appendChild(node);
    }

    function collegaVariante(node, vidx) {
      var sbagliateContainer = node.querySelector(".sbagliate-container");
      node.querySelector(".btn-aggiungi-sbagliata").addEventListener("click", function () {
        aggiungiSbagliata(sbagliateContainer, vidx);
      });
      node.querySelector(".btn-rimuovi-variante").addEventListener("click", function () {
        node.remove();
      });
      sbagliateContainer.querySelectorAll(".btn-rimuovi-sbagliata").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.closest("div").remove(); });
      });
    }

    function aggiungiVariante() {
      var vidx = nextVidx++;
      var node = tplVariante.content.firstElementChild.cloneNode(true);
      var campoVidx = node.querySelector(".campo-vidx");
      campoVidx.name = "variante_idx";
      campoVidx.value = vidx;
      node.querySelector(".campo-testo").name = "testo_" + vidx;
      node.querySelector(".campo-corretta").name = "corretta_" + vidx;
      collegaVariante(node, vidx);
      container.appendChild(node);
      aggiungiSbagliata(node.querySelector(".sbagliate-container"), vidx);
      aggiornaVisibilitaRisposte();
    }

    blocchiEsistenti.forEach(function (node) {
      var vidx = parseInt(node.querySelector(".campo-vidx").value, 10);
      collegaVariante(node, vidx);
    });

    // una domanda aperta non ha risposte a scelta multipla: nasconde quei campi in ogni
    // variante (il server li ignora comunque, ma mostrarli confonderebbe inutilmente).
    // Dichiarata prima di aggiungiVariante(), che la richiama subito se il form parte senza varianti.
    var campoAperta = form.querySelector(".campo-aperta");
    function aggiornaVisibilitaRisposte() {
      var aperta = campoAperta.checked;
      form.querySelectorAll(".blocco-risposte-multiple").forEach(function (blocco) {
        blocco.style.display = aperta ? "none" : "";
      });
    }
    campoAperta.addEventListener("change", aggiornaVisibilitaRisposte);
    aggiornaVisibilitaRisposte();

    form.querySelector(".btn-aggiungi-variante").addEventListener("click", aggiungiVariante);
    if (blocchiEsistenti.length === 0) aggiungiVariante();

    // Stessa regola del server (_valida_varianti): almeno una variante con un testo; per
    // ognuna di queste (a meno che l'esercizio sia una domanda aperta) serve anche la
    // risposta corretta e almeno una sbagliata. Validare qui, prima dell'invio, evita che
    // un campo mancante mandi il form al server (che risponderebbe con un redirect,
    // perdendo tutto quello già compilato): l'utente resta sulla stessa pagina, con tutti
    // i campi intatti, e viene guidato dritto al campo da completare.
    var erroreDiv = form.querySelector(".errore-form-esercizio");
    function mostraErrore(msg, campoDaFocalizzare) {
      erroreDiv.textContent = msg;
      erroreDiv.style.display = "";
      erroreDiv.scrollIntoView({ behavior: "smooth", block: "center" });
      if (campoDaFocalizzare) campoDaFocalizzare.focus();
    }
    form.addEventListener("submit", function (ev) {
      erroreDiv.style.display = "none";
      var blocchi = Array.prototype.slice.call(container.querySelectorAll(".variante-block"));
      var candidate = blocchi.filter(function (b) { return b.querySelector(".campo-testo").value.trim(); });
      if (candidate.length === 0) {
        ev.preventDefault();
        var primoTesto = blocchi.length ? blocchi[0].querySelector(".campo-testo") : null;
        mostraErrore("Serve almeno una variante con un testo del quesito compilato.", primoTesto);
        return;
      }
      if (!campoAperta.checked) {
        for (var i = 0; i < candidate.length; i++) {
          var blocco = candidate[i];
          var corretta = blocco.querySelector(".campo-corretta");
          var sbagliate = Array.prototype.slice.call(blocco.querySelectorAll(".campo-sbagliata"));
          var haSbagliata = sbagliate.some(function (c) { return c.value.trim(); });
          if (!corretta.value.trim()) {
            ev.preventDefault();
            mostraErrore("Manca la risposta corretta in una variante con il testo già compilato.", corretta);
            return;
          }
          if (!haSbagliata) {
            ev.preventDefault();
            var campoSbagliata = sbagliate.length ? sbagliate[0] : null;
            mostraErrore("Manca almeno una risposta sbagliata in una variante con il testo già compilato.", campoSbagliata);
            return;
          }
          // due risposte uguali (a meno di spazi e maiuscole) sarebbero indistinguibili sul foglio
          var viste = {};
          var campi = [corretta].concat(sbagliate).filter(function (c) { return c.value.trim(); });
          for (var k = 0; k < campi.length; k++) {
            var chiave = campi[k].value.replace(/\s+/g, "").toLowerCase();
            if (viste[chiave]) {
              ev.preventDefault();
              mostraErrore("Due risposte della stessa variante sono uguali (\"" + campi[k].value.trim() + "\"): cambiane una.", campi[k]);
              return;
            }
            viste[chiave] = true;
          }
        }
      }
    });

    if (form.dataset.avvisaRigenerazione) {
      form.addEventListener("submit", function (ev) {
        if (ev.defaultPrevented) return;
        var msg = "Questo esercizio è già usato in almeno un blocco di compiti generato. " +
          "Salvare le modifiche rigenererà quei blocchi con il nuovo testo/varianti, eliminando " +
          "i vecchi codici (non è possibile se hanno già risultati registrati). Procedere comunque?";
        if (!window.confirm(msg)) ev.preventDefault();
      });
    }
  });
}

// Riga a scomparsa che, alla prima apertura, carica il proprio contenuto da `url` (es.
// anteprima o modulo di modifica di un esercizio) e poi richiama `dopo(riga)`.
function caricaInRiga(riga, url, dopo) {
  var destinazione = riga.querySelector("td") || riga;
  if (riga.dataset.caricata) { if (dopo) dopo(riga); return; }
  destinazione.innerHTML = '<p class="muted">Caricamento…</p>';
  fetch(url).then(function (r) { return r.text(); }).then(function (html) {
    destinazione.innerHTML = html;
    riga.dataset.caricata = "1";
    if (dopo) dopo(riga);
  });
}

// Copia un testo negli appunti; dove il browser non lo permette (pagina aperta da un
// indirizzo non sicuro, es. IP di rete) lo mostra in una casella già selezionata.
function copiaTesto(testo, doveMostrare) {
  function mostra() {
    var area = document.createElement("textarea");
    area.rows = 8;
    area.value = testo;
    area.readOnly = true;
    doveMostrare.appendChild(area);
    area.focus();
    area.select();
    return Promise.resolve(false);
  }
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(testo).then(function () { return true; }, mostra);
  }
  return mostra();
}

// Pannello "Genera con l'IA" (_genera_ia.html), inserito via fetch nella scheda Testo.
function attivaPannelloIA(radice) {
  var form = radice.querySelector(".form-genera-ia");
  if (!form) return;
  var stato = form.querySelector(".stato-ia");
  form.addEventListener("submit", function (ev) {
    if (ev.defaultPrevented) return;  // es. annullato dall'avviso sui blocchi già generati
    var btn = form.querySelector(".btn-genera-ia");
    btn.disabled = true;
    btn.textContent = "Generazione in corso…";
    stato.textContent = "Claude sta preparando gli esercizi: di solito 1–3 minuti, non chiudere la pagina.";
  });
  form.querySelector(".btn-prompt-ia").addEventListener("click", function () {
    var parametri = new URLSearchParams({
      n: form.elements.n.value, varianti: form.elements.varianti.value, istruzioni: form.elements.istruzioni.value,
    });
    stato.textContent = "Preparazione del prompt…";
    fetch(form.getAttribute("action").replace(/\/genera$/, "/prompt") + "?" + parametri)
      .then(function (r) { return r.text(); })
      .then(function (testo) { return copiaTesto(testo, form); })
      .then(function (copiato) {
        stato.textContent = copiato
          ? "Prompt copiato: incollalo in claude.ai, poi incolla la risposta in \"Importa un compito da un unico file\"."
          : "Copia il prompt dalla casella qui sotto.";
      });
  });
}

// Riquadro di una pagina che si aggiorna da solo: i moduli al suo interno che corrispondono
// a `selettore` vengono inviati in background (con l'intestazione X-Frammento) e il server
// risponde con il riquadro ridisegnato, che sostituisce il contenuto di `contenitore`. Così
// non si ricarica l'intera pagina dell'appello a ogni aula aggiunta o iscritto tolto. Le
// tendine con data-chiave restano aperte/chiuse com'erano; data-conferma su un modulo
// chiede conferma prima dell'invio; `dopo(contenitore)` riattiva quel che serve (es. i
// suggerimenti dei campi), anche la prima volta.
function attivaRiquadro(contenitore, selettore, dopo) {
  contenitore.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (ev.defaultPrevented || !form.matches(selettore)) return;
    ev.preventDefault();
    if (form.dataset.conferma && !window.confirm(form.dataset.conferma)) return;
    var aperti = {};
    contenitore.querySelectorAll("details[data-chiave]").forEach(function (d) { aperti[d.dataset.chiave] = d.open; });
    var focus = document.activeElement && document.activeElement.name;
    var dati = new FormData(form);  // prima di disabilitare: i campi disabilitati non vengono inviati
    form.querySelectorAll("button, select").forEach(function (el) { el.disabled = true; });
    fetch(form.action, { method: "POST", body: dati, headers: { "X-Frammento": "1" } })
      .then(function (r) {
        if (!r.ok) throw new Error("errore " + r.status);
        return r.text();
      })
      .then(function (html) {
        contenitore.innerHTML = html;
        contenitore.querySelectorAll("details[data-chiave]").forEach(function (d) {
          if (aperti[d.dataset.chiave] !== undefined) d.open = aperti[d.dataset.chiave];
        });
        if (dopo) dopo(contenitore);
        // il cursore torna sul campo appena usato (o sul primo campo di testo del riquadro),
        // pronto per il prossimo inserimento
        var campo = (focus && contenitore.querySelector('input[name="' + focus + '"]:not([type=hidden])'))
          || contenitore.querySelector('input[name="matricola"]:not([type=hidden])');
        if (campo) campo.focus({ preventScroll: true });
      })
      .catch(function (err) {
        form.querySelectorAll("button, select").forEach(function (el) { el.disabled = false; });
        alert("Non sono riuscito a salvare: " + err.message);
      });
  });
  if (dopo) dopo(contenitore);
}

// Suggerimenti di studenti del corso per un campo matricola: scegliere un nome dalla
// lista riempie solo il campo e mostra sotto chi è stato scelto; si aggiunge davvero
// solo col pulsante del modulo (che prende il focus: basta Invio per confermare).
function _autocompleteStudenti(campo, suggerimenti, tag) {
  if (!campo) return;
  var scelto = document.createElement("div");
  scelto.className = "muted studente-scelto";
  scelto.hidden = true;
  suggerimenti.parentNode.appendChild(scelto);
  suggerimenti.addEventListener("click", function (ev) {
    var voce = ev.target.closest(".sfoglia-voce");
    if (!voce) return;
    scelto.textContent = "Scelto: " + voce.textContent + " — premi il pulsante per confermare";
    scelto.hidden = false;
    var pulsante = campo.form && campo.form.querySelector('button[type="submit"]');
    if (pulsante) pulsante.focus();
  });
  campo.addEventListener("input", function () { scelto.hidden = true; });
  creaAutocomplete(
    campo, suggerimenti,
    function (q) { return "/corsi/" + tag + "/api/studenti/cerca?q=" + encodeURIComponent(q); },
    function (s) { return s.cognome + " " + s.nome + " (" + s.matricola + ")"; },
    function (s) { return s.matricola; }
  );
}

// Riquadro "Studenti ammessi e aule" di una prova (_ammessi_aule.html): tutti i suoi moduli.
function attivaAmmessiAule(riquadro, tag) {
  attivaRiquadro(riquadro, "form", function (r) {
    r.open = true;
    _autocompleteStudenti(r.querySelector(".campo-input-matricola-aula"), r.querySelector(".campo-suggerimenti-aula"), tag);
  });
}

// Ridisegna il riquadro "Studenti ammessi e aule" di una prova (url dell'appello senza
// barra finale) quando cambia qualcosa da cui dipende: gli iscritti o le aule.
function ridisegnaAmmessi(riquadro, urlAppello, tag) {
  if (!riquadro) return;
  fetch(urlAppello + "/ammessi-aule", { headers: { "X-Frammento": "1" } })
    .then(function (r) { return r.ok ? r.text() : null; })
    .then(function (html) {
      if (html === null) return;
      var aperti = {};
      riquadro.querySelectorAll("details[data-chiave]").forEach(function (d) { aperti[d.dataset.chiave] = d.open; });
      riquadro.innerHTML = html;
      riquadro.querySelectorAll("details[data-chiave]").forEach(function (d) {
        if (aperti[d.dataset.chiave] !== undefined) d.open = aperti[d.dataset.chiave];
      });
      _autocompleteStudenti(riquadro.querySelector(".campo-input-matricola-aula"), riquadro.querySelector(".campo-suggerimenti-aula"), tag);
    });
}

// Aule di una prova nella scheda Impostazioni (_aule.html): dopo ogni modifica si aggiorna
// anche il riquadro degli ammessi della stessa prova, nella scheda Compiti d'esame.
function attivaAule(contenitore, tag) {
  var primaVolta = true;
  attivaRiquadro(contenitore, "form", function (c) {
    if (primaVolta) { primaVolta = false; return; }
    var id = c.closest(".blocco-aule").dataset.appello;
    ridisegnaAmmessi(document.querySelector('.tab-pane[data-pane="compiti-' + id + '"] .blocco-ammessi-aule'),
      "/corsi/" + tag + "/appelli/" + id, tag);
  });
}

// Elenco iscritti di un appello (_iscritti.html). Dopo ogni modifica aggiorna anche ciò
// che ne dipende nella stessa scheda: il conteggio nel titolo, il numero di compiti
// suggerito e, per una prova di un raggruppamento, il riquadro degli ammessi.
function attivaIscritti(contenitore, tag) {
  var primaVolta = true;
  // una sola tendina di strumenti aperta alla volta
  contenitore.addEventListener("click", function (ev) {
    var summary = ev.target.closest("details.strumento > summary");
    if (!summary) return;
    contenitore.querySelectorAll("details.strumento[open]").forEach(function (d) { if (d !== summary.parentElement) d.open = false; });
  });
  // esclusi dal filtro sulla matricola: la freccia apre sotto la riga la carriera dello
  // studente in tutti i corsi, caricata alla prima apertura
  contenitore.addEventListener("click", function (ev) {
    var btn = ev.target.closest(".btn-carriera");
    if (!btn) return;
    var riga = contenitore.querySelector('.riga-carriera[data-matricola="' + btn.dataset.matricola + '"]');
    riga.hidden = !riga.hidden;
    btn.setAttribute("aria-expanded", riga.hidden ? "false" : "true");
    if (!riga.hidden) caricaInRiga(riga, riga.querySelector("td").dataset.url);
  });
  attivaRiquadro(contenitore, ".form-frammento", function (c) {
    _autocompleteStudenti(c.querySelector(".campo-input-matricola-iscritto"), c.querySelector(".campo-suggerimenti-studenti-iscritto"), tag);
    if (primaVolta) { primaVolta = false; return; }
    var dati = c.querySelector(".riquadro-iscritti").dataset;
    var sezione = c.closest("details");
    var conteggio = sezione && sezione.querySelector(".conteggio-iscritti");
    if (conteggio) conteggio.textContent = dati.numero ? "(" + dati.numero + ")" : "";
    var scheda = c.closest(".tab-pane") || document;
    var numero = scheda.querySelector('.form-genera-compiti input[name="numero_studenti"]');
    if (numero && dati.suggerito) numero.value = dati.suggerito;
    var form = c.querySelector(".barra-aggiungi");
    if (form) ridisegnaAmmessi(scheda.querySelector(".blocco-ammessi-aule"), form.action.replace(/\/iscritti-manuale\/aggiungi$/, ""), tag);
  });
}

// Sezioni del flusso di lavoro di un appello (<details class="sezione-flusso">): il
// template le apre se la fase è ancora da fare e le richiude se è completata, così
// risaltano i passaggi che mancano. Se il docente apre o chiude a mano una sezione, la
// scelta resta per la sessione (anche dopo i ricaricamenti dovuti alle azioni) finché la
// fase non cambia stato: la chiave include data-fatta, quindi quando una fase viene
// completata la sezione torna a richiudersi da sola.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("details.sezione-flusso[data-sezione]").forEach(function (d) {
    var chiave = "sezione-flusso:" + location.pathname + ":" + d.dataset.sezione + ":" + d.dataset.fatta;
    try {
      var salvato = sessionStorage.getItem(chiave);
      if (salvato !== null) d.open = salvato === "1";
    } catch (e) { /* storage non disponibile: resta lo stato predefinito */ }
    d.addEventListener("toggle", function () {
      try { sessionStorage.setItem(chiave, d.open ? "1" : "0"); } catch (e) { /* ignorato */ }
    });
  });
});

// Barre di schede che scorrono in orizzontale (schermi stretti): all'apertura della
// pagina porta in vista la scheda attiva, che altrimenti potrebbe restare fuori schermo.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".schede-sezione").forEach(function (barra) {
    var attiva = barra.querySelector("a.active");
    if (attiva && barra.scrollWidth > barra.clientWidth) {
      barra.scrollLeft = attiva.offsetLeft - (barra.clientWidth - attiva.offsetWidth) / 2;
    }
  });
});

// Le icone "i" (_info.html) stanno spesso nel titolo di una sezione a scomparsa: cliccarle
// mostra la nota, senza aprire o chiudere la sezione.
document.addEventListener("click", function (ev) {
  var icona = ev.target.closest && ev.target.closest("summary .aiuto");
  if (icona) { ev.preventDefault(); icona.focus(); }
});

// Matita accanto al cestino (aule, prove di un raggruppamento, configurazioni IA): apre e
// chiude il modulo di modifica sotto la riga, data-modifica="<id del modulo>"; il suo
// pulsante Annulla (data-chiudi-modifica) lo richiude scartando quanto scritto.
document.addEventListener("click", function (ev) {
  var matita = ev.target.closest && ev.target.closest("[data-modifica]");
  if (matita) {
    var riga = document.getElementById(matita.dataset.modifica);
    if (!riga) return;
    riga.hidden = !riga.hidden;
    matita.setAttribute("aria-expanded", riga.hidden ? "false" : "true");
    var campo = !riga.hidden && riga.querySelector("input:not([type=hidden]), select");
    if (campo) campo.focus();
    return;
  }
  var annulla = ev.target.closest && ev.target.closest("[data-chiudi-modifica]");
  if (annulla) {
    var modulo = annulla.closest("[data-riga-modifica]");
    if (!modulo) return;
    var form = modulo.querySelector("form");
    if (form) form.reset();
    modulo.hidden = true;
  }
});

// La nota si apre centrata sotto l'icona: vicino al bordo della finestra (es. un'icona
// nell'ultima colonna di una tabella) uscirebbe di lato, quindi la si sposta all'interno.
function _riposizionaNota(ev) {
  var icona = ev.target.closest && ev.target.closest(".aiuto");
  var nota = icona && icona.querySelector(".aiuto-testo");
  if (!nota) return;
  nota.style.transform = "";
  var r = nota.getBoundingClientRect();
  if (!r.width) return;  // non ancora visibile
  var margine = 8, dx = 0;
  if (r.right > window.innerWidth - margine) dx = window.innerWidth - margine - r.right;
  if (r.left + dx < margine) dx = margine - r.left;
  if (dx) nota.style.transform = "translateX(calc(-50% + " + Math.round(dx) + "px))";
}
document.addEventListener("mouseover", _riposizionaNota);
document.addEventListener("focusin", _riposizionaNota);

// --- Scheda Testo di un appello (_tab_creazione.html / _testo_esercizi.html) -----------
// Le azioni sugli esercizi assegnati (assegna dalla banca, togli, obbligatorio, crea,
// modifica, importa da altri corsi) vengono inviate in background: il server risponde con
// il riquadro ridisegnato (X-Frammento), senza ricaricare tutta la pagina dell'appello.
// Importa da file e Genera con l'IA portano invece a una pagina di conferma, come prima.
var _AZIONI_TESTO_IN_BACKGROUND = ["/obbligatorio", "/rimuovi", "/esercizi/assegna", "/esercizi/nuovo", "/modifica", "/importa-da-altri-corsi"];
var _AZIONI_TESTO_RIGENERANO = _AZIONI_TESTO_IN_BACKGROUND.concat(["/importa-json", "/ia/genera"]);

function attivaTesto(sezione) {
  var tag = sezione.dataset.tag;
  function dati() { return sezione.querySelector(".dati-testo").dataset; }

  sezione.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (ev.defaultPrevented) return;
    var action = form.getAttribute("action") || "";
    var fine = function (suffissi) { return suffissi.some(function (s) { return action.endsWith(s); }); };
    if (form.classList.contains("form-rimuovi-esercizio")) {
      var nome = form.dataset.nome;
      if (!window.confirm("Togliere l'esercizio " + nome + " da questo compito?")) { ev.preventDefault(); return; }
      var elimina = window.confirm(
        "Eliminare l'esercizio " + nome + " anche dalla banca dati del corso?\n\n" +
        "OK = elimina definitivamente anche dalla banca\n" +
        "Annulla = lo tolgo solo da questo compito (resta nella banca, riusabile)");
      form.querySelector('[name="elimina_dalla_banca"]').value = elimina ? "1" : "";
    }
    if (form.classList.contains("form-chiudi-testo") && form.dataset.attesi !== "" &&
        form.dataset.obbligatori !== form.dataset.attesi && !window.confirm(
      "Il testo ha " + form.dataset.obbligatori + " esercizi obbligatori, ma le impostazioni del corso ne " +
      "prevedono " + form.dataset.attesi + ". Chiudere comunque il testo?")) {
      ev.preventDefault(); return;
    }
    var blocchi = Number(dati().blocchi);
    // il modulo di modifica chiede già lui conferma (data-avvisa-rigenerazione)
    if (blocchi && fine(_AZIONI_TESTO_RIGENERANO) && !action.endsWith("/modifica") && !window.confirm(
      "Sono già stati generati " + blocchi + " blocco/i di compiti per questo appello. Modificare gli esercizi " +
      "assegnati rigenererà il file di riferimento e tutti i blocchi già generati, eliminando i vecchi codici " +
      "(non è possibile se qualche blocco ha già risultati registrati). Procedere comunque?")) {
      ev.preventDefault(); return;
    }
    if (!fine(_AZIONI_TESTO_IN_BACKGROUND)) return;  // invio normale (pagina di conferma)
    ev.preventDefault();
    var corpo = new FormData(form);
    form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
    fetch(form.action, { method: "POST", body: corpo, headers: { "X-Frammento": "1", "X-Appello": sezione.dataset.appello } })
      .then(function (r) { if (!r.ok) throw new Error("errore " + r.status); return r.text(); })
      .then(function (html) {
        var aperto = sezione.dataset.pannelloAperto;
        // dopo aver creato un esercizio il modulo si richiude; dopo un'assegnazione resta la banca
        if (action.endsWith("/esercizi/nuovo")) aperto = "";
        sezione.innerHTML = html;
        sezione.open = true;
        _inizializzaTesto(sezione, tag, aperto);
        _aggiornaDopoTesto(sezione);
        var flash = sezione.querySelector(".flash");
        if (flash && flash.getBoundingClientRect().top < 0) flash.scrollIntoView({ block: "center" });
      })
      .catch(function (err) {
        form.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
        alert("Non sono riuscito a salvare: " + err.message);
      });
  });
  _inizializzaTesto(sezione, tag, "");
}

// Ciò che nella pagina dipende dagli esercizi assegnati, fuori dal riquadro: la fase Testo
// del percorso (il pulsante "Genera" dei compiti resta spento finché il testo è aperto).
function _aggiornaDopoTesto(sezione) {
  var d = sezione.querySelector(".dati-testo").dataset;
  var n = Number(d.n);
  var passo = document.querySelector('.passo a[data-tab="' + sezione.dataset.scheda + '"]');
  if (passo) {
    var li = passo.parentElement;
    // il riquadro si ridisegna solo a testo aperto: con esercizi la fase è in corso
    // (si completa chiudendo il testo, che ricarica la pagina)
    li.classList.remove("passo-fatta");
    li.classList.toggle("passo-in_corso", n > 0);
    li.classList.toggle("passo-da_fare", n === 0);
    var icona = passo.querySelector(".passo-icona");
    var segno = icona.querySelector(".passo-segno");
    if (segno) segno.remove();
    if (n > 0) icona.insertAdjacentHTML("beforeend", '<span class="passo-segno">•</span>');
    passo.dataset.info = "Testo — " + (n > 0 ? "In corso" : "Da fare") + ": " + n + " esercizi, testo ancora aperto";
  }
}

function _inizializzaTesto(sezione, tag, pannelloAperto) {
  // barra degli strumenti: un pannello alla volta, quelli pesanti caricati alla prima apertura
  function apri(nome) {
    sezione.dataset.pannelloAperto = nome || "";
    sezione.querySelectorAll(".pannello-testo").forEach(function (p) {
      var questo = p.dataset.pannello === nome;
      p.hidden = !questo;
      if (questo && p.dataset.url && !p.dataset.caricato) {
        p.dataset.caricato = "1";
        fetch(p.dataset.url).then(function (r) { return r.text(); }).then(function (html) {
          p.innerHTML = html;
          if (p.dataset.pannello === "ia") attivaPannelloIA(p);
          else attivaElencoEsercizi(p.querySelector(".elenco-esercizi-corsi"));
        });
      }
    });
    sezione.querySelectorAll("[data-apri]").forEach(function (b) {
      var attivo = b.dataset.apri === nome;
      b.classList.toggle("attivo", attivo);
      b.setAttribute("aria-expanded", attivo ? "true" : "false");
    });
  }
  sezione.querySelectorAll("[data-apri]").forEach(function (b) {
    b.addEventListener("click", function () { apri(sezione.dataset.pannelloAperto === b.dataset.apri ? "" : b.dataset.apri); });
  });
  if (pannelloAperto && sezione.querySelector('[data-apri="' + pannelloAperto + '"]')) apri(pannelloAperto);
  inizializzaFormEsercizi(sezione);

  // anteprima/modifica di un esercizio assegnato, in una riga sotto
  function chiudiRighe(eccetto) {
    sezione.querySelectorAll(".riga-anteprima-assegnato, .riga-modifica-assegnato").forEach(function (r) {
      if (r !== eccetto) r.style.display = "none";
    });
  }
  function caricaAnteprima(riga, id) {
    if (riga.dataset.caricata) { renderizzaLatex(riga); return; }
    fetch("/corsi/" + tag + "/esercizi/" + id + "/anteprima").then(function (r) { return r.text(); }).then(function (html) {
      riga.querySelector("td").innerHTML = html;
      riga.dataset.caricata = "1";
      renderizzaLatex(riga);
    });
  }
  sezione.querySelectorAll(".btn-anteprima-assegnato").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var riga = sezione.querySelector('.riga-anteprima-assegnato[data-esercizio="' + btn.dataset.esercizio + '"]');
      var giaAperta = riga.style.display !== "none";
      chiudiRighe(giaAperta ? null : riga);
      riga.style.display = giaAperta ? "none" : "";
      if (!giaAperta) caricaAnteprima(riga, btn.dataset.esercizio);
    });
  });
  sezione.querySelectorAll(".btn-modifica-assegnato").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var riga = sezione.querySelector('.riga-modifica-assegnato[data-esercizio="' + btn.dataset.esercizio + '"]');
      var giaAperta = riga.style.display !== "none";
      chiudiRighe(giaAperta ? null : riga);
      riga.style.display = giaAperta ? "none" : "";
      if (!giaAperta) caricaInRiga(riga, riga.querySelector("td").dataset.url, inizializzaFormEsercizi);
    });
  });

  // banca del corso: filtri e anteprima
  var ricerca = sezione.querySelector(".filtro-ricerca");
  if (ricerca) {
    var argomento = sezione.querySelector(".filtro-argomento");
    var righe = sezione.querySelectorAll(".tabella-disponibili tr[data-nome]");
    var applica = function () {
      var testo = ricerca.value.trim().toLowerCase();
      var arg = argomento.value;
      righe.forEach(function (riga) {
        var ok = (!testo || riga.dataset.nome.includes(testo) || riga.dataset.testo.includes(testo)) && (!arg || riga.dataset.argomento === arg);
        riga.style.display = ok ? "" : "none";
        var anteprima = riga.nextElementSibling;
        if (!ok && anteprima) anteprima.style.display = "none";
      });
    };
    ricerca.addEventListener("input", applica);
    argomento.addEventListener("change", applica);
    sezione.querySelectorAll(".btn-anteprima-disponibile").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var riga = sezione.querySelector('.riga-anteprima-disponibile[data-esercizio="' + btn.dataset.esercizio + '"]');
        var giaAperta = riga.style.display !== "none";
        riga.style.display = giaAperta ? "none" : "";
        if (!giaAperta) caricaAnteprima(riga, btn.dataset.esercizio);
      });
    });
  }

  _attivaRiordinoEsercizi(sezione);
}

// Riordino degli esercizi assegnati: ogni esercizio è un <tbody> (la riga principale più
// quelle, nascoste, di anteprima e modifica), che si sposta tutto insieme. Ogni modifica
// salva subito l'ordine completo, senza ricaricare la pagina.
function _attivaRiordinoEsercizi(sezione) {
  var tabella = sezione.querySelector(".tabella-esercizi-assegnati");
  if (!tabella || !tabella.querySelector(".maniglia-ordine")) return;
  var stato = sezione.querySelector(".stato-ordine");
  function voci() { return Array.prototype.slice.call(tabella.querySelectorAll("tbody.esercizio-assegnato")); }
  function aggiornaNumeri() {
    var tutte = voci();
    tutte.forEach(function (tb, i) {
      tb.querySelector(".posizione-ordine").value = i + 1;
      tb.querySelector(".btn-ordine-su").disabled = i === 0;
      tb.querySelector(".btn-ordine-giu").disabled = i === tutte.length - 1;
    });
  }
  var timer = null;
  function salva() {
    aggiornaNumeri();
    stato.textContent = "Salvataggio…";
    clearTimeout(timer);
    // più spostamenti ravvicinati (es. frecce premute di seguito) diventano un solo salvataggio
    timer = setTimeout(function () {
      fetch(tabella.dataset.urlOrdine, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ordine: voci().map(function (tb) { return Number(tb.dataset.esercizio); }) }),
      }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) { stato.textContent = res.ok ? "Ordine salvato ✓" : "Errore: " + res.d.errore; })
        .catch(function () { stato.textContent = "Errore: ordine non salvato"; });
    }, 400);
  }
  function sposta(tb, posizione) {
    var tutte = voci().filter(function (x) { return x !== tb; });
    posizione = Math.max(0, Math.min(tutte.length, posizione));
    tabella.insertBefore(tb, tutte[posizione] || null);
    salva();
  }
  voci().forEach(function (tb) {
    tb.querySelector(".btn-ordine-su").addEventListener("click", function () { sposta(tb, voci().indexOf(tb) - 1); });
    tb.querySelector(".btn-ordine-giu").addEventListener("click", function () { sposta(tb, voci().indexOf(tb) + 1); });
    var campo = tb.querySelector(".posizione-ordine");
    function applica() {
      var n = parseInt(campo.value, 10);
      if (isNaN(n) || n - 1 === voci().indexOf(tb)) { aggiornaNumeri(); return; }
      sposta(tb, n - 1);
    }
    campo.addEventListener("change", applica);
    campo.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); applica(); } });
    var maniglia = tb.querySelector(".maniglia-ordine");
    maniglia.addEventListener("dragstart", function (ev) {
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", tb.dataset.esercizio);
      tabella.dataset.trascinato = tb.dataset.esercizio;
      tb.classList.add("trascinato");
    });
    maniglia.addEventListener("dragend", function () {
      tb.classList.remove("trascinato");
      delete tabella.dataset.trascinato;
    });
    tb.addEventListener("dragover", function (ev) {
      var id = tabella.dataset.trascinato;
      if (!id || id === tb.dataset.esercizio) return;
      ev.preventDefault();
      var trascinato = tabella.querySelector('tbody.esercizio-assegnato[data-esercizio="' + id + '"]');
      // sopra o sotto a seconda di quale metà della riga ha il puntatore
      var r = tb.getBoundingClientRect();
      tabella.insertBefore(trascinato, ev.clientY < r.top + r.height / 2 ? tb : tb.nextSibling);
    });
    tb.addEventListener("drop", function (ev) { ev.preventDefault(); salva(); });
  });
  var ordinaPer = sezione.querySelector(".ordina-per");
  ordinaPer.addEventListener("change", function () {
    var criterio = ordinaPer.value;
    ordinaPer.value = "";
    if (!criterio) return;
    var tutte = voci();
    if (criterio === "inverti") {
      tutte.reverse();
    } else {
      var chiave = {
        nome: function (tb) { return [tb.dataset.nome]; },
        argomento: function (tb) { return [tb.dataset.argomento || "￿", tb.dataset.nome]; },
        difficolta: function (tb) { return [Number(tb.dataset.difficolta) || 99]; },
        obbligatorio: function (tb) { return [1 - Number(tb.dataset.obbligatorio)]; },
        aperta: function (tb) { return [Number(tb.dataset.aperta)]; },
      }[criterio];
      tutte.sort(function (a, b) {
        var ka = chiave(a), kb = chiave(b);
        for (var i = 0; i < ka.length; i++) {
          if (ka[i] === kb[i]) continue;
          return typeof ka[i] === "number" ? ka[i] - kb[i] : ka[i].localeCompare(kb[i], "it", { numeric: true });
        }
        return 0;
      });  // stabile: a parità resta l'ordine attuale
    }
    tutte.forEach(function (tb) { tabella.appendChild(tb); });
    salva();
  });
  aggiornaNumeri();
}

// Pulsante "Annulla" accanto a "Salva" (class="btn-annulla" dentro un modulo): attivo solo
// quando il modulo ha modifiche non salvate, le scarta riportando i campi ai valori salvati.
// Con data-annulla="ricarica" sul modulo (es. righe aggiunte via JavaScript, che reset()
// non toglierebbe) ricarica invece la pagina.
document.addEventListener("input", _segnaModuloModificato, true);
document.addEventListener("change", _segnaModuloModificato, true);
function _segnaModuloModificato(ev) {
  var form = ev.target && ev.target.form;
  if (!form || form.dataset.annullando) return;
  form.querySelectorAll(".btn-annulla").forEach(function (b) { b.disabled = false; });
}
document.addEventListener("click", function (ev) {
  var btn = ev.target.closest && ev.target.closest(".btn-annulla");
  if (!btn || !btn.form) return;
  var form = btn.form;
  if (form.dataset.annulla === "ricarica") {
    form.dataset.scartato = "1";
    location.reload();
    return;
  }
  form.dataset.annullando = "1";
  form.reset();
  // i campi che mostrano o nascondono altre parti del modulo si aggiornano di conseguenza
  form.querySelectorAll("input[type=checkbox], input[type=radio], select").forEach(function (el) {
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  delete form.dataset.annullando;
  form.querySelectorAll(".btn-annulla").forEach(function (b) { b.disabled = true; });
  form.dispatchEvent(new CustomEvent("annullato"));
});

// Correzione degli svolgimenti: mentre si scrive la nota di uno svolgimento, le note simili
// già scritte per lo stesso esercizio in altri compiti (studente e punteggio), per dare lo
// stesso giudizio agli stessi errori. Discreto: una riga in piccolo, l'elenco al passaggio
// del mouse (o col focus da tastiera). `voci`: [{nota, punteggio, studente}].
function _paroleNota(testo) {
  return (testo || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .split(/[^a-z0-9]+/).filter(function (p) { return p.length >= 3; });
}
function noteSimili(testo, voci) {
  var cercate = _paroleNota(testo);
  var frase = (testo || "").trim().toLowerCase();
  if (!frase) return [];
  return voci.map(function (v) {
    var parole = _paroleNota(v.nota);
    var comuni = cercate.filter(function (p) {
      // anche parole solo iniziate ("sbagl" trova "sbagliato") o con desinenza diversa
      return parole.some(function (q) { return q === p || (p.length >= 4 && q.indexOf(p) === 0) || (q.length >= 4 && p.indexOf(q) === 0) || (p.length >= 5 && q.length >= 5 && p.slice(0, 5) === q.slice(0, 5)); });
    }).length;
    var somiglianza = v.nota.trim().toLowerCase() === frase ? 1
      : (cercate.length && parole.length ? comuni / Math.min(cercate.length, parole.length) : 0);
    return { voce: v, somiglianza: somiglianza };
  }).filter(function (x) { return x.somiglianza >= 0.5; })
    .sort(function (a, b) { return b.somiglianza - a.somiglianza; })
    .map(function (x) { return x.voce; });
}
function mostraNoteSimili(box, testo, voci, punteggioCorrente) {
  var simili = noteSimili(testo, voci);
  box.innerHTML = "";
  box.className = "note-simili";
  if (!simili.length) return;
  var punteggi = simili.map(function (v) { return v.punteggio; }).filter(function (p) { return p !== null && p !== undefined; });
  var diverso = punteggioCorrente !== "" && punteggioCorrente !== null && punteggi.length &&
    punteggi.indexOf(parseInt(punteggioCorrente, 10)) === -1;
  var riga = document.createElement("span");
  riga.className = "note-simili-riga";
  riga.tabIndex = 0;
  riga.textContent = "≈ " + simili.length + " not" + (simili.length === 1 ? "a simile" : "e simili") +
    (punteggi.length ? " · punteggi " + punteggi.slice(0, 6).join(", ") + (punteggi.length > 6 ? "…" : "") : "") +
    (diverso ? " · qui " + punteggioCorrente : "");
  if (diverso) box.classList.add("diverso");
  var elenco = document.createElement("ul");
  elenco.className = "note-simili-elenco";
  simili.slice(0, 12).forEach(function (v) {
    var li = document.createElement("li");
    var p = document.createElement("strong");
    p.textContent = v.punteggio === null || v.punteggio === undefined ? "–" : v.punteggio;
    var chi = document.createElement("span");
    chi.className = "muted";
    chi.textContent = v.studente;
    var nota = document.createElement("span");
    nota.textContent = v.nota;
    li.appendChild(p); li.appendChild(chi); li.appendChild(nota);
    elenco.appendChild(li);
  });
  if (simili.length > 12) {
    var altre = document.createElement("li");
    altre.className = "muted";
    altre.textContent = "e altre " + (simili.length - 12);
    elenco.appendChild(altre);
  }
  riga.appendChild(elenco);
  box.appendChild(riga);
}

// Nome di uno studente in un elenco (_studente.html): apre/chiude sotto la riga la sua
// carriera in breve, caricata la prima volta. Vale per tutte le tabelle della pagina,
// anche quelle ridisegnate dopo un'azione.
document.addEventListener("click", function (ev) {
  var btn = ev.target.closest(".apri-carriera");
  if (!btn) return;
  ev.preventDefault();
  var riga = btn.closest("tr");
  var url = btn.dataset.url;
  var box = riga ? riga.nextElementSibling : btn.nextElementSibling;
  if (box && box.classList.contains("carriera-aperta") && box.dataset.url === url) {
    box.hidden = !box.hidden;
    btn.setAttribute("aria-expanded", String(!box.hidden));
    return;
  }
  var dove;
  if (riga) {
    box = document.createElement("tr");
    var colonne = 0;
    Array.prototype.forEach.call(riga.cells, function (c) { colonne += c.colSpan || 1; });
    box.innerHTML = '<td colspan="' + colonne + '"></td>';
    dove = box.firstChild;
    riga.parentNode.insertBefore(box, riga.nextSibling);
  } else {
    box = document.createElement("div");
    dove = box;
    btn.parentNode.insertBefore(box, btn.nextSibling);
  }
  box.className = "carriera-aperta";
  box.dataset.url = url;
  dove.innerHTML = '<span class="muted">carico…</span>';
  btn.setAttribute("aria-expanded", "true");
  fetch(url).then(function (r) { return r.text(); }).then(function (html) { dove.innerHTML = '<div class="box-carriera">' + html + "</div>"; });
});
