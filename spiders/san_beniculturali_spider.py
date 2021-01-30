import scrapy
import requests
from items import ImageItem

class SanBeniculturaliSpider(scrapy.Spider):
    name = "SanBeniculturaliDownloader"
    # List of URL of registries to download, can be an "Archivio di stato", a city, a registry, a year or only an image
    # WARNING: if too general the dimension on disk could be very high
    # Lista degli URL dei registri da scaricare, può essere un archivio di stato, una città, un registro, un anno e una sola immagine
    # ATTENZIONE: se troppo generali, la dimensione su disco può diventare molto grande
    start_urls = [
    'http://dl.antenati.san.beniculturali.it/v/Archivio+di+Stato+di+Modena/Stato+civile+della+restaurazione/Modena/',
    'http://dl.antenati.san.beniculturali.it/v/Archivio+di+Stato+di+Modena/Stato+civile+italiano/Modena/',
    ]


    def parse(self, response):
        # If is an image page, save it
        # Se è un'immagine, la salva
        if 'jpg' in response.url.split('.'):
            yield ImageItem(image_urls=[response.xpath('//*[@id="zoomAntenati1"]').attrib['href']],
            image_name=[response.url.replace('.jpg', '').replace(':', '').replace('.html', '')[41:]])
        else:
            # Iterate over the elements in the central page
            # Itera sugli elementi a centro pagina
            table = response.xpath('//*[@id="gsThumbMatrix"]//a')
            for elem in table:
                yield response.follow(elem.attrib['href'], callback=self.parse)
            
            # Check if there are multiple pages
            # Controlla se ci sono più pagine
            next_page = response.css('div.next-and-last a::attr(href)').get()   
            if next_page is not None:
                yield response.follow(next_page, callback=self.parse)
                