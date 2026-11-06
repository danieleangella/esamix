"""Chiave di sblocco (facoltativa, dalle Impostazioni): se impostata, le operazioni
pericolose (ripristini, eliminazioni, migrazione, verbalizzazioni) e, a scelta, anche
l'inserimento e la modifica degli esiti chiedono la chiave. Le pagine restano tutte
visibili. Sbloccata, vale per la sessione del browser finché non resta inattiva più dei
minuti impostati (o finché l'app non si riavvia). Della chiave si conserva solo
un'impronta (PBKDF2 con sale), mai la chiave in chiaro."""
import hashlib
import hmac
import re
import secrets
import time

from quizesame.services import app_config as app_config_service

COOKIE = "esamix_sblocco"
MINUTI_PREDEFINITI = 30
_ITERAZIONI = 200_000

# sessioni sbloccate: token del cookie -> ultimo momento di attività
_sessioni: dict[str, float] = {}

_APPELLO = r"^/corsi/[^/]+/appelli/\d+"
PERICOLOSE = [re.compile(p) for p in (
    r"^/ripristina-backup", _APPELLO + r"/ripristina", r"^/elimina-tutti-i-dati", r"^/migrazione/esegui",
    r"^/impostazioni/corso-prova/elimina", r"^/impostazioni/chiave$",
    r"^/corsi/[^/]+/elimina$", _APPELLO + r"/elimina$", _APPELLO + r"/blocchi/\d+/elimina",
    r"^/corsi/[^/]+/studenti/[^/]+/elimina", r"^/corsi/[^/]+/esercizi/\d+/elimina",
    _APPELLO + r"/verbalizza",
)]
# inserimento e modifica degli esiti (protetti solo se scelto nelle Impostazioni)
ESITI = [re.compile(p) for p in (
    _APPELLO + r"/correggi", _APPELLO + r"/svolgimenti/valuta", _APPELLO + r"/assenze/segna",
    _APPELLO + r"/annulla-compito", _APPELLO + r"/risultati/[^/]+/(elimina|modifica)",
    _APPELLO + r"/orale/[^/]+/completa", _APPELLO + r"/revisione-testo", _APPELLO + r"/rifiuta/",
    _APPELLO + r"/presenze/(chiudi|segna-restanti)",
)]


def _impronta(chiave: str, sale: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", chiave.encode("utf-8"), bytes.fromhex(sale), _ITERAZIONI).hex()


def attiva() -> bool:
    return bool(app_config_service.get_settings().chiave_impronta)


def verifica(chiave: str) -> bool:
    s = app_config_service.get_settings()
    if not s.chiave_impronta:
        return True
    return hmac.compare_digest(_impronta(chiave or "", s.chiave_sale), s.chiave_impronta)


def imposta(chiave: str, protegge_correzione: bool, minuti: int) -> None:
    """Imposta (o cambia) la chiave; con `chiave` vuota la toglie."""
    if not chiave:
        app_config_service.update_settings(chiave_impronta="", chiave_sale="")
        _sessioni.clear()
        return
    if len(chiave) < 4:
        raise ValueError("La chiave deve avere almeno 4 caratteri")
    sale = secrets.token_hex(16)
    app_config_service.update_settings(
        chiave_impronta=_impronta(chiave, sale), chiave_sale=sale,
        chiave_correzione=protegge_correzione, chiave_minuti=max(1, minuti),
    )


def imposta_opzioni(protegge_correzione: bool, minuti: int) -> None:
    app_config_service.update_settings(chiave_correzione=protegge_correzione, chiave_minuti=max(1, minuti))


def protetta(metodo: str, percorso: str) -> bool:
    """Se questa richiesta chiede la chiave (le pagine si vedono sempre: si proteggono le
    azioni, cioè i POST, più l'apertura della modifica di un esito)."""
    s = app_config_service.get_settings()
    if not s.chiave_impronta:
        return False
    if metodo == "POST" and any(p.search(percorso) for p in PERICOLOSE):
        return True
    if not s.chiave_correzione:
        return False
    return (metodo == "POST" or percorso.endswith("/modifica")) and any(p.search(percorso) for p in ESITI)


def sbloccata(token: str) -> bool:
    """Sessione sbloccata e ancora attiva: ogni richiesta ne rinnova l'attività."""
    if not token or token not in _sessioni:
        return False
    minuti = app_config_service.get_settings().chiave_minuti or MINUTI_PREDEFINITI
    adesso = time.time()
    if adesso - _sessioni[token] > minuti * 60:
        del _sessioni[token]
        return False
    _sessioni[token] = adesso
    return True


def nuova_sessione() -> str:
    token = secrets.token_urlsafe(24)
    _sessioni[token] = time.time()
    return token


def chiudi_sessione(token: str) -> None:
    _sessioni.pop(token, None)
