# Requisiti e specifiche – antenati-dl 1.0

Documento di analisi redatto per la riscrittura del progetto *SanBeniculturali-Downloader*
(novembre 2026).

## 1. Stato di partenza

La versione 0.3 del progetto (PyQt5) scaricava le immagini dal vecchio portale
`http://dl.antenati.san.beniculturali.it/gallery2/`, basato su *Gallery2*: la navigazione
avveniva seguendo i link `giTitle` delle gallerie e le immagini venivano salvate leggendo la pagina
di dettaglio di ogni foto.

Quel sito non esiste più. Dal 2021 il **Portale Antenati** è
`https://antenati.cultura.gov.it` (WordPress + Solr + IIIF), quindi:

- tutti gli URL e i selettori del vecchio codice sono invalidi;
- l'app non riusciva più nemmeno a caricare la lista iniziale;
- il codice aveva inoltre problemi strutturali (path relativi alla cwd, thread/process misti,
  busy-wait nella GUI, nessun resume, nessun controllo dello spazio su disco).

## 2. Analisi del nuovo portale

| Livello | Sorgente | Note |
|---|---|---|
| Archivi di Stato | `/esplora-gli-archivi/` | 98 archivi; id numerico ricavato dai popup della mappa (`/archive/?archivio=ID`) |
| Fondi | `/archive/?archivio=ID` | blocchi `data-property` (es. *Stato civile italiano*, *napoleonico*, *della restaurazione*) |
| Serie | stessa pagina, link `/search-registry/?serie=ID` | una serie per comune / quartiere |
| Tipologia, Anno | faccette `tipologia_ss`, `anni_is` di `/search-registry/` | conteggi per ogni valore |
| Registro | risultato di ricerca → `/ark:/12657/an_uaNNNN/` | la pagina contiene `manifestId` |
| Pagine | manifest IIIF Presentation 2 su `dam-antenati.cultura.gov.it` | immagini IIIF Image API 2 su `iiif-antenati.cultura.gov.it` (Cantaloupe) |

Parametri di ricerca: `archivio`, `serie`, `localita`, `tipologia`, `anno`, `nominativi`,
`s_facet_query` (termini di faccetta uniti dal separatore **letterale** `%2C`), `s_page` (base 1),
`s_size` (fino a 1000), `s_sort` (`estremoRemoto_i asc|desc`).

### Vincoli tecnici scoperti

1. **User-Agent**: il portale (AWS ALB) risponde 403 a client non-browser → serve uno UA da browser.
2. **Referer**: manifest e immagini (Cloudflare) richiedono `Referer: https://antenati.cultura.gov.it/`.
3. **`full/full` bloccato**: l'URL IIIF canonico per l'originale è bloccato dal firewall
   ("Sorry, you have been blocked"). La risoluzione originale si ottiene con
   `full/pct:100/0/default.jpg` (o `full/<larghezza>,`); `full/max` è limitato a ~10 MP.
4. **Risposte vuote**: il server immagini a volte risponde `200` con corpo vuoto → retry con
   validazione del JPEG.
5. **Bug di ricerca**: tipologia + anno + un altro filtro (serie/località) restituisce sempre 0
   risultati (anche sul sito). Workaround: si interroga senza anno e si filtra lato client
   sull'intervallo di anni del titolo del registro.
6. `s_size=1` restituisce 0 risultati → dimensione minima 2.
7. Il manifest dichiara dimensioni fittizie (1000×1000); quelle reali sono in `info.json`.

## 3. Requisiti funzionali

| ID | Requisito |
|---|---|
| RF1 | Navigare la gerarchia Archivio → Fondo → Serie → Tipologia → Anno → Registro → Pagine, con conteggi |
| RF2 | Ricerca libera per località / tipologia / anno / archivio |
| RF3 | Aprire un registro da id (`an_ua…`), URL ark, URL di pagina o URL di manifest |
| RF4 | Scaricare un registro intero o solo alcune pagine (intervalli `1-10,15,20-`, selezione in GUI) |
| RF5 | Scaricare in blocco tutto ciò che sta sotto un livello (archivio, fondo, serie, tipologia, anno) |
| RF6 | Stima prima del download in blocco (registri, pagine, byte) e conferma esplicita |
| RF7 | Risoluzione selezionabile: originale, `max` (~10 MP), larghezza personalizzata |
| RF8 | Resume: le pagine già presenti vengono saltate; scrittura atomica (`.part` → rename) |
| RF9 | Metadati per registro (`register.json`) e PDF opzionale (`img2pdf`) |
| RF10 | Interfaccia **CLI** (comandi + navigatore interattivo) e **GUI** desktop |
| RF11 | Avanzamento in tempo reale, annullamento pulito, riepilogo finale |
| RF12 | Controllo dello spazio libero su disco prima di ogni registro |

## 4. Requisiti non funzionali

- Windows, Linux, macOS; Python ≥ 3.10; distribuibile con `uv`/`pip` e come eseguibile PyInstaller.
- Rispetto del servizio pubblico: 4 download paralleli di default (max 16), retry con backoff
  esponenziale, rispetto di `Retry-After` su 429/503.
- Rete sempre fuori dal thread GUI; GUI reattiva.
- Parsing isolato in funzioni pure testate offline su fixture reali; test live opzionali
  (`pytest -m network`).
- Struttura su disco leggibile:
  `Download/<Archivio>/<Fondo>/<Serie>/<Tipologia>/<Titolo> (<an_ua id>)/0001.jpg`.

## 5. Architettura

```
src/antenati_dl/
  http.py      sessione HTTP (UA, Referer, retry, validazione immagini, BlockedError)
  models.py    dataclass condivise (Archive, Fondo, Serie, SearchQuery, Manifest, Page, eventi…)
  portal.py    parser puri + classe Portal con cache (archivi, fondi, ricerca, manifest)
  download.py  Downloader: thread pool, resume, eventi di avanzamento, stima, PDF
  utils.py     intervalli di pagine, nomi file sicuri, formattazione
  cli.py       interfaccia a riga di comando (argparse + rich)
  gui/         interfaccia grafica (PySide6)
```

CLI e GUI usano lo stesso motore: il `Downloader` emette `ProgressEvent` che la CLI mostra con
barre `rich` e la GUI inoltra al thread principale tramite segnali Qt.

## 6. Fuori ambito / sviluppi futuri

- Ricerca per nominativi (indici dei nomi) – richiede autenticazione/flussi diversi.
- Download parziali di riquadri (tile) e mosaico: non necessari, `pct:100` fornisce l'originale.
