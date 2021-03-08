# SanBeniculturali-Downloader
This project is intended to be a simple application to allow the download of genealogy registers from the site http://www.antenati.san.beniculturali.it/
In fact, it is enough to choose which register, or which year, or which city or other and it downloads all the required images on your device.

This application has the only purpose of allowing the automatic saving on your device of the scans of the registers in order to be able to consult them more quickly or simply at a later time, several times and especially in the absence of internet connection.
I am not responsible for using this program for any purpose other than simply saving images on your private device.

## WARNING

The size of the download depending on the city, registry, archive or other chosen can reach several hundred gigabytes, so always check the space available on the storage media, as well as what you are about to download.
At the moment there is no verification of the available space and the required space by the download.

I strongly advise against downloading a state archive completely, but rather downloading only the city concerned or, even better, the single year or single category of registers (eg only the registers relating to the indices).

## How to Run
### Windows

From [release page](https://github.com/MarcelloCuoghi/SanBeniculturali-Downloader/releases/latest) , extract the ZIP file and launch the file "sanbeniculturali_downloader.exe"

### Unix

Depending on your operating system, some steps may be differents, here I show the step for working with Ubuntu.
Open line command:

1) Install Python 3 `sudo apt update && sudo apt install python3 && sudo apt install python3-pip`
2) Install requirements `pip3 install -r requirements.txt`
3) Start the application `python3 sanbeniculturali_downloader.py`

### MacOs

I don't own a macos system, so I could not try, but I think it's working similar to unix.

# ITA

Questo progetto vuole essere una semplice applicazione per permettere il download dei registri di genealogia dal sito http://www.antenati.san.beniculturali.it/
Basta infatti scegliere quale registro, oppure quale anno, oppure quale città o altro ed esso scarica tutte le immagini richieste sul proprio dispositivo.

Questa applicazione ha il solo scopo di permettere il salvataggio automatico sul proprio dispositivo delle scansioni dei registri in modo da poterli consultare più velocemente o anche semplicemente in un secondo momento, in più volte e sopratutto in mancanza di connessione internet. 
Non sono responsabile dell'utilizzo di questo programma per altri scopi diversi da quello del semplice salvataggio delle immagini sul proprio dispositivo privato.

## ATTENZIONE
Le dimensioni del download a seconda della città, del registro, dell'archivio o altro scelto possono raggiungere diverse centinaia di gigabyte, per cui controllare sempre lo spazio disponibile sul supporto di archiviazione, nonchè cosa si sta per scaricare.
Al momento non è presente nessun controllo o verifica a priori dello spazio disponibile e dello spazio richiesto dal download.

Sconsiglio vivamente di scaricare completamente un archivio di stato, ma piuttosto di scaricare la sola città interessata o, ancora meglio, il singolo anno o la singola categoria di registri (per es i soli registri relativi agli indici).

## Come avviarlo
### Windows

Dalla [pagina release](https://github.com/MarcelloCuoghi/SanBeniculturali-Downloader/releases/latest), scarica ed estrai il file ZIP, avvia poi il file "sanbeniculturali_downloader.exe"

### Unix

A seconda del sistema operativo, alcuni passaggi potrebbero essere differenti (ma google aiuta), qui sono mostrati quelli necessari con Ubuntu.
Apri la linea di comando:

1) Installa Python 3 `sudo apt update && sudo apt install python3 && sudo apt install python3-pip`
2) Installa i requisiti `pip3 install -r requirements.txt`
3) Avvia l'applicazione `python3 sanbeniculturali_downloader.py`

### MacOs

Non possiedo un sistema MacOs, e quindi non riesco a provare, ma penso funzioni in modo simile a unix.
