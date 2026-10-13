"""Impostazioni dell'app nel suo complesso (non di un singolo corso): nome del docente,
mostrato nel menu in alto, se mostrare il riquadro di riepilogo generale in homepage e
le configurazioni per l'IA. Salvate in un file JSON accanto alle cartelle dei corsi, non
in un database per corso, perché valgono per l'intera installazione."""
import json
import uuid
from dataclasses import asdict, dataclass, field

from quizesame import config

SETTINGS_PATH = config.DATA_ROOT / "app_settings.json"

FORNITORI_IA = ("anthropic", "openrouter", "ollama")


@dataclass
class AppSettings:
    docente: str = ""
    mostra_riepilogo_home: bool = True
    # Configurazioni per l'IA (vedi services/ia.py): se ne possono salvare più d'una (es.
    # una chiave personale e una del dipartimento, o un server Ollama) e scegliere di volta
    # in volta quale usare. Ognuna è {"id", "nome", "fornitore" (anthropic | openrouter |
    # ollama), "chiave", "url", "modello", "token"}: chiave per Anthropic/OpenRouter,
    # url/token per Ollama, modello facoltativo (vuoto = quello predefinito del fornitore).
    # Le chiavi restano solo in questo file sul computer e non vengono mai rimostrate.
    ia_profili: list = field(default_factory=list)
    ia_profilo_predefinito: str = ""


def nuovo_id_profilo() -> str:
    return uuid.uuid4().hex[:8]


def _profili_da_vecchie_impostazioni(data: dict) -> tuple[list, str]:
    """Le versioni precedenti salvavano una sola chiave per fornitore: diventano una
    configurazione ciascuna, e il fornitore scelto diventa quella predefinita."""
    profili = []
    if data.get("anthropic_api_key"):
        profili.append({"id": "anthropic", "nome": "Anthropic", "fornitore": "anthropic",
                        "chiave": data["anthropic_api_key"], "url": "", "modello": "", "token": ""})
    if data.get("openrouter_api_key"):
        profili.append({"id": "openrouter", "nome": "OpenRouter", "fornitore": "openrouter",
                        "chiave": data["openrouter_api_key"], "url": "", "modello": "", "token": ""})
    if data.get("ollama_url") and data.get("ollama_modello"):
        profili.append({"id": "ollama", "nome": "Ollama", "fornitore": "ollama", "chiave": "",
                        "url": data["ollama_url"], "modello": data["ollama_modello"], "token": data.get("ollama_token", "")})
    scelto = data.get("ia_fornitore", "")
    predefinito = scelto if any(p["id"] == scelto for p in profili) else (profili[0]["id"] if profili else "")
    return profili, predefinito


def get_settings() -> AppSettings:
    if not SETTINGS_PATH.exists():
        return AppSettings()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return AppSettings()
    if "ia_profili" in data:
        profili = [p for p in data.get("ia_profili") or [] if isinstance(p, dict) and p.get("fornitore") in FORNITORI_IA]
        predefinito = data.get("ia_profilo_predefinito", "")
    else:
        profili, predefinito = _profili_da_vecchie_impostazioni(data)
    return AppSettings(
        docente=data.get("docente", ""),
        mostra_riepilogo_home=bool(data.get("mostra_riepilogo_home", True)),
        ia_profili=profili,
        ia_profilo_predefinito=predefinito,
    )


def update_settings(**campi) -> None:
    attuali = asdict(get_settings())
    attuali.update(campi)
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(attuali, indent=2, ensure_ascii=False), encoding="utf-8")
