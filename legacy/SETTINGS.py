# specifico
APPELLO_IDX = "settimo-appello"
NUMERO_STUDENTI = 0

# generali
CORSO = "Geometria O-Z"
FACOLTA = "Ingegneria Meccanica, Ingegneria Gestionale"
UNIVERSITA = "Universit\\`a di Firenze"
ANNO = "2024/2025"
TAG = "GeoOZ-IngMEL-UniFi25"
DOCENTE = "Daniele Angella"

# date
APPELLO = {"prima-prova-parziale": "06/11/2025",\
           "seconda-prova-parziale": "22/12/2025",\
           "seconda-prova-parziale-variante": "18/12/2025",\
           "prove-parziali": "16/01/2026",\
           "primo-appello": "16/01/2026",\
           "secondo-appello": "20/02/2026",\
           "terzo-appello": "09/04/2026",\
           "quarto-appello": "15/06/2026",\
           "quinto-appello": "29/06/2026",\
           "sesto-appello": "20/07/2026",\
           "settimo-appello": "10/09/2026",\
           "primo-appello-straordinario": "dd/mm/yyyy",\
           "secondo-appello-straordinario": "dd/mm/yyyy",\
           }

# testi
NUMEX = 11
NUMFILE = 2

#consegna brutta
CONSEGNA = 4

# valutazione
RISPOSTA_VUOTA = 0
RISPOSTA_CORRETTA = 3
RISPOSTA_SBAGLIATA = -1


VOTOMIN_VERB = 18
VOTOMIN_PARZIALI = 15
VOTOMIN_APPELLO = 18


if "parzial" in APPELLO_IDX:
    VOTOMIN = VOTOMIN_PARZIALI
else:
    VOTOMIN = VOTOMIN_APPELLO
