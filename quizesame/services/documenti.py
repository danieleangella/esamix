"""Versioni Markdown dei documenti da pubblicare online (fogli di riferimento e risultati):
stesso contenuto dei PDF, in un testo semplice da incollare in Moodle, in una pagina web o
in un'email. Le formule restano in LaTeX tra $...$, come le leggono Moodle e MathJax."""
from quizesame.services import compiti as compiti_service
from quizesame.services import corsi as corsi_service
from quizesame.services import risultati as risultati_service
from quizesame.services.latex import LETTERE
from quizesame.services.testo_web import testo_md


def _intestazione(corso, appello, titolo: str) -> list[str]:
    righe = [f"# {titolo}", "", f"**{corso.nome}**" + (f" ({corso.anno})" if corso.anno else "")]
    dettagli = " · ".join(p for p in (corso.facolta, corso.universita) if p)
    if dettagli:
        righe[-1] += "  "  # due spazi finali: a capo in Markdown, senza iniziare un nuovo paragrafo
        righe.append(dettagli)
    righe += ["", f"**{appello.nome}**" + (f" — {appello.data}" if appello.data else ""), ""]
    return righe


def riferimento_md(tag: str, appello_id: int, con_soluzioni: bool = False) -> str:
    """Come il foglio di riferimento PDF: un compito completo per ogni variante (Compito A
    con la prima variante di ogni esercizio, B con la seconda...), la risposta corretta
    segnata e, con `con_soluzioni`, la soluzione sotto ogni esercizio che ne ha una."""
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    esercizi = compiti_service._esercizi_per_generazione(tag, appello_id)
    n_varianti = max((len(e["varianti"]) for e in esercizi), default=0)
    titolo = "Testo con soluzioni" if con_soluzioni else "Testo"
    righe = _intestazione(corso, appello, titolo)
    for k in range(n_varianti):
        righe += ["---", "", f"## Compito {LETTERE.get(k, k + 1)}", ""]
        for i, e in enumerate(esercizi, start=1):
            v = e["varianti"][k % len(e["varianti"])]
            etichette = [t for t, si in (("obbligatorio", e["obbligatorio"]), ("domanda aperta", e["aperta"])) if si]
            righe.append(f"### Esercizio {i}" + (f" ({', '.join(etichette)})" if etichette else ""))
            righe += ["", testo_md(v["testo"]).strip(), ""]
            for j, r in enumerate(v["risposte"]):
                righe.append(f"- **{LETTERE.get(j, j + 1)}.** {testo_md(r)}" + (" ✓" if j == 0 else ""))
            if v["risposte"]:
                righe.append("")
            if con_soluzioni and e.get("soluzione"):
                # ogni riga col suo ">": altrimenti le righe successive escono dal riquadro
                # (o vi si fondono in un'unica riga, a seconda del lettore Markdown)
                soluzione = f"**Soluzione / suggerimento.** {testo_md(e['soluzione']).strip()}"
                righe += [f"> {r}".rstrip() for r in soluzione.splitlines()] + [""]
    return "\n".join(righe).rstrip() + "\n"


def risultati_md(tag: str, appello_id: int) -> str:
    """L'elenco dei risultati da pubblicare: matricola parzialmente oscurata, iniziali ed
    esito, come nel PDF."""
    corso = corsi_service.get_corso(tag)
    appello = corsi_service.get_appello(tag, appello_id)
    righe = _intestazione(corso, appello, "Risultati della prova scritta")
    righe += ["Le matricole sono parzialmente oscurate; tra parentesi le iniziali di nome e cognome.", "",
              "| Matricola | Iniziali | Esito |", "|---|:---:|---|"]
    for r in risultati_service.lista_risultati_html(tag, appello_id):
        iniziali = f"{(r['nome'] or ' ')[0]}{(r['cognome'] or ' ')[0]}".strip()
        righe.append(f"| `{r['matricola']}` | {iniziali} | **{r['etichetta']}** |")
    return "\n".join(righe) + "\n"
