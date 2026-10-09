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
