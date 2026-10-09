"""Impostazioni dell'app nel suo complesso (non di un singolo corso): nome del docente,
mostrato nel menu in alto, e se mostrare il riquadro di riepilogo generale in homepage.
Salvate in un file JSON accanto alle cartelle dei corsi, non in un database per corso,
perché valgono per l'intera installazione."""
import json
from dataclasses import asdict, dataclass

from quizesame import config

SETTINGS_PATH = config.DATA_ROOT / "app_settings.json"


@dataclass
class AppSettings:
    docente: str = ""
    mostra_riepilogo_home: bool = True
    # chiave per la generazione di esercizi con Claude (vedi services/ia.py): resta solo
    # in questo file sul computer, e non viene mai rimostrata per intero nelle pagine
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""  # alternativa ad Anthropic diretto, stessa API (vedi ia.py)
    # Ollama su un proprio server: indirizzo (es. http://192.168.1.10:11434), modello
    # installato sul server, e token facoltativo se il server è dietro un proxy con login
    ollama_url: str = ""
    ollama_modello: str = ""
    ollama_token: str = ""
    # fornitore scelto esplicitamente ("anthropic" | "openrouter" | "ollama"); vuoto =
    # il primo configurato, nell'ordine
    ia_fornitore: str = ""


def get_settings() -> AppSettings:
    if not SETTINGS_PATH.exists():
        return AppSettings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppSettings()
    return AppSettings(
        docente=data.get("docente", ""),
        mostra_riepilogo_home=bool(data.get("mostra_riepilogo_home", True)),
        anthropic_api_key=data.get("anthropic_api_key", ""),
        openrouter_api_key=data.get("openrouter_api_key", ""),
        ollama_url=data.get("ollama_url", ""),
        ollama_modello=data.get("ollama_modello", ""),
        ollama_token=data.get("ollama_token", ""),
        ia_fornitore=data.get("ia_fornitore", ""),
    )


def update_settings(**campi) -> None:
    attuali = asdict(get_settings())
    attuali.update(campi)
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(attuali, indent=2, ensure_ascii=False), encoding="utf-8")
