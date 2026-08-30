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
    )


def update_settings(**campi) -> None:
    attuali = asdict(get_settings())
    attuali.update(campi)
    config.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(attuali, indent=2, ensure_ascii=False), encoding="utf-8")
