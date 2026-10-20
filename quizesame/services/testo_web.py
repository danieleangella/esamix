"""Testi degli esercizi (scritti in LaTeX per i PDF) resi leggibili fuori da LaTeX: nelle
pagine HTML, dove le formule le disegna KaTeX, e nei documenti Markdown da pubblicare.

Le formule restano in LaTeX; si traducono solo i comandi di testo che né il browser né
KaTeX capiscono (accenti come \\`e, {\\bfseries ...}) e gli ambienti che KaTeX non conosce
(eqnarray*)."""
import re

from markupsafe import Markup, escape

_ACCENTI = {
    "`": dict(zip("aeiouAEIOU", "àèìòùÀÈÌÒÙ")),
    "'": dict(zip("aeiouAEIOU", "áéíóúÁÉÍÓÚ")),
}
_RE_ACCENTO = re.compile(r"\\([`'])\{?([aeiouAEIOU])\}?")
# accento scritto dopo la lettera ("\e'" invece di "\'e"): un refuso che LaTeX tollera
_RE_ACCENTO_INVERTITO = re.compile(r"\\([aeiouAEIOU])'")
_RE_GRASSETTO = re.compile(r"\{\\bfseries\s*([^{}]*)\}|\\textbf\{([^{}]*)\}")
_RE_EQNARRAY = re.compile(r"\\begin\{eqnarray\*?\}(.*?)\\end\{eqnarray\*?\}", re.S)
_RE_DISPLAY = re.compile(r"[ \t]*\n?[ \t]*(\\\[.*?\\\]|\$\$.*?\$\$)[ \t]*\n?", re.S)


def accenti(testo: str) -> str:
    t = _RE_ACCENTO.sub(lambda m: _ACCENTI[m.group(1)][m.group(2)], testo or "")
    return _RE_ACCENTO_INVERTITO.sub(lambda m: _ACCENTI["`"][m.group(1)], t)


def _grassetto(testo: str, apri: str, chiudi: str) -> str:
    return _RE_GRASSETTO.sub(lambda m: apri + (m.group(1) or m.group(2) or "").strip() + chiudi, testo)


def testo_md(testo: str) -> str:
    """Per i documenti Markdown: le formule restano come sono (Moodle e MathJax leggono
    anche \\[...\\] ed eqnarray)."""
    return _grassetto(accenti(testo), "**", "**")


def testo_html(testo: str) -> Markup:
    """Per le pagine HTML (testo già escapato, da mostrare così com'è): eqnarray* diventa
    un array a tre colonne equivalente, che KaTeX sa disegnare."""
    t = accenti(testo)
    t = _RE_EQNARRAY.sub(lambda m: "\\[\\begin{array}{rcl}" + m.group(1) + "\\end{array}\\]", t)
    t = t.replace("\\lefteqn{", "\\mathrlap{")
    # una formula in display va già a capo da sola: gli a capo del sorgente attorno a
    # \[...\] (mostrati dal white-space: pre-wrap delle pagine) lascerebbero righe vuote
    t = _RE_DISPLAY.sub(lambda m: m.group(1), t)
    return Markup(_grassetto(str(escape(t)), "<strong>", "</strong>"))
