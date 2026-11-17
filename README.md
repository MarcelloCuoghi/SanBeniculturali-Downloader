# SanBeniculturali-Downloader (antenati-dl)

Browse and download the genealogical registers published on the **Portale Antenati**
([antenati.cultura.gov.it](https://antenati.cultura.gov.it)) – from a desktop **GUI** or from the
**command line**. Download entire archives, a single register, or just a few pages.

> 🇮🇹 La versione italiana è [più sotto](#ita).

Version 1.0 is a complete rewrite: the old portal (`dl.antenati.san.beniculturali.it`, Gallery2)
was shut down years ago and the 0.x releases no longer work. The new version speaks the current
portal (WordPress search + IIIF image server). See [docs/REQUISITI.md](docs/REQUISITI.md) for the
analysis of the new site.

## Features

- Navigate the portal hierarchy: **Archivio → Fondo → Serie (comune) → Tipologia → Anno → Registro → Pagine**, with counts at every level
- Free search by place / record type / year
- Open a register from its id (`an_ua…`), its ark URL, the URL of a single page, or its IIIF manifest
- Download a whole register, a page selection (`1-10,15,20-`), or **everything under any level** (archive, fondo, serie, type, year)
- Size estimate and confirmation before bulk downloads, free-disk-space check
- Original resolution (default), `max` (~10 MP) or a custom width
- Resumable: pages already on disk are skipped, files are written atomically
- `register.json` metadata for every register, optional PDF per register
- Parallel downloads with retries and back-off (polite defaults)

## ⚠️ Warning

A whole archive can be **hundreds of gigabytes** (a page at full resolution is ~0.5–1 MB, an
archive has hundreds of thousands of pages). Prefer downloading a single comune, record type or
year. The tool shows an estimate and asks for confirmation before bulk downloads.

The images belong to the Italian Ministry of Culture and the Archivi di Stato – see the portal's
[terms](https://antenati.cultura.gov.it/note-legali/). This tool only automates saving images for
personal research and offline consultation; use it responsibly and do not overload the service.

## Installation

### Windows executable
Download the ZIP from the [releases page](https://github.com/MarcelloCuoghi/SanBeniculturali-Downloader/releases/latest),
extract it and run `antenati-dl-gui.exe` (GUI) or `antenati-dl.exe` (command line).

### With uv (any OS, recommended)
```bash
git clone https://github.com/MarcelloCuoghi/SanBeniculturali-Downloader.git
cd SanBeniculturali-Downloader
uv sync --extra gui --extra pdf      # CLI only: uv sync
uv run antenati-dl-gui               # GUI
uv run antenati-dl --help            # CLI
```

### With pip
```bash
python -m pip install ".[gui,pdf]"
antenati-dl-gui
antenati-dl --help
```
`python sanbeniculturali_downloader.py` still works from a source checkout: without arguments it
opens the GUI, with arguments it runs the CLI.

## GUI

```
antenati-dl-gui        (or: antenati-dl gui)
```

- **Left**: the navigation tree (archives → fondi → series → types → years → registers), with a
  filter box and a "Go to register" field accepting ids or portal URLs.
- **Right**: the selected register's metadata and page thumbnails. Select pages (Ctrl/Shift-click
  or a range such as `1-10,15`), double-click a page for a large preview. For any other level, a
  summary with a *Download everything under this node* button (with estimate).
- **Bottom**: the download queue with progress and cancel.
- **Settings**: destination folder, image size, parallel downloads, overwrite, PDF creation.

## Command line

```bash
antenati-dl archives modena                       # list archives (id, name)
antenati-dl fondi 156                             # fondi of an archive (id or name)
antenati-dl series 156 --fondo "Stato civile italiano" carpi
antenati-dl browse --serie 37842016               # record types available in a serie
antenati-dl browse --serie 37842016 --tipologia Nati            # years
antenati-dl browse --serie 37842016 --tipologia Nati --anno 1911 --registers
antenati-dl search --localita Modena --tipologia Nati --anno 1870
antenati-dl info an_ua37845354 --pages            # register metadata and pages

# download
antenati-dl download an_ua37845354                          # whole register
antenati-dl download https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/ --pages 1-10,15
antenati-dl download-all --serie 37842016 --tipologia Nati  # every "Nati" register of a comune
antenati-dl download-all --archive 145 --dry-run            # just estimate
antenati-dl interactive                                     # menu-driven navigator
```

Common download options: `-o/--output DIR` (default `./Download`), `--size full|max|WIDTH`,
`-j/--workers N` (default 4), `--overwrite`, `--pdf`, `--no-metadata`, `--dry-run`, `--yes`.
Interrupt with Ctrl+C at any time and run the same command again to resume.

## Output layout

```
Download/
└── Archivio di Stato di Vibo Valentia/
    └── Stato civile italiano/
        └── Acquaro/
            └── Morti/
                └── 1911 (an_ua37845354)/
                    ├── 0001.jpg … 0055.jpg
                    ├── register.json        # metadata, page labels, source URLs
                    └── register.pdf         # with --pdf
```

## Development

```bash
uv sync --extra gui --extra pdf
uv run pytest                  # offline test-suite (fixtures from the real portal)
uv run pytest -m network       # a few live tests against the portal
uv run ruff check . && uv run ruff format --check .
uv run --with pyinstaller pyinstaller packaging/antenati-dl.spec --noconfirm   # Windows bundle
```

Code layout: `http.py` (session, headers, retries), `portal.py` (parsers + cached client),
`download.py` (engine), `cli.py`, `gui/`. Technical notes on the portal are in
[docs/REQUISITI.md](docs/REQUISITI.md).

---

<a id="ita"></a>
# ITA

Applicazione per **sfogliare e scaricare** i registri dello stato civile pubblicati sul
**Portale Antenati** ([antenati.cultura.gov.it](https://antenati.cultura.gov.it)), con
**interfaccia grafica** o da **riga di comando**: archivi interi, un singolo registro o solo
alcune pagine.

La versione 1.0 è una riscrittura completa: il vecchio portale (`dl.antenati.san.beniculturali.it`)
è stato dismesso da anni e le versioni 0.x non funzionano più. L'analisi del nuovo sito e i
requisiti sono in [docs/REQUISITI.md](docs/REQUISITI.md).

## Funzionalità

- Navigazione **Archivio → Fondo → Serie (comune) → Tipologia → Anno → Registro → Pagine**, con i conteggi
- Ricerca per località / tipologia / anno
- Apertura di un registro da id (`an_ua…`), URL ark, URL di una pagina o manifest IIIF
- Download di un registro intero, di una selezione di pagine (`1-10,15,20-`) o di **tutto quanto sta sotto un livello**
- Stima e conferma prima dei download massivi, controllo dello spazio libero
- Risoluzione originale (predefinita), `max` (~10 MP) o larghezza a scelta
- Ripresa automatica: le pagine già scaricate vengono saltate
- Metadati `register.json` per ogni registro e PDF opzionale

## ⚠️ Attenzione

Un archivio intero può occupare **centinaia di gigabyte**. Meglio scaricare il singolo comune, la
singola tipologia o il singolo anno. Prima di un download massivo l'app mostra una stima e chiede
conferma. Le immagini appartengono al Ministero della Cultura e agli Archivi di Stato: questo
programma serve solo a salvarle per la ricerca personale e la consultazione offline. Non sono
responsabile di usi diversi; non sovraccaricare il servizio.

## Come avviarlo

- **Windows**: dalla [pagina release](https://github.com/MarcelloCuoghi/SanBeniculturali-Downloader/releases/latest)
  scarica lo ZIP, estrailo e avvia `antenati-dl-gui.exe` (grafica) o `antenati-dl.exe` (riga di comando).
- **Linux / macOS / Windows con Python**: installa [uv](https://docs.astral.sh/uv/) e poi
  ```bash
  uv sync --extra gui --extra pdf
  uv run antenati-dl-gui         # interfaccia grafica
  uv run antenati-dl --help      # riga di comando
  ```
  oppure `python -m pip install ".[gui,pdf]"`.

## Uso rapido

- **GUI**: a sinistra l'albero degli archivi; selezionando un registro compaiono le miniature
  delle pagine, che si possono selezionare e scaricare; su qualsiasi altro livello il pulsante
  *Download everything under this node* scarica tutto (dopo la stima). In basso la coda dei download.
- **CLI**: `antenati-dl interactive` apre un navigatore a menu; per gli script ci sono i comandi
  `archives`, `fondi`, `series`, `browse`, `search`, `info`, `download`, `download-all`
  (vedi esempi nella sezione inglese). Ctrl+C interrompe; rilanciando lo stesso comando il download
  riprende da dove si era fermato.
