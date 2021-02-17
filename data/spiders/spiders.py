import scrapy
from data.items import ImageItem


class SanBeniculturaliDownloader(scrapy.Spider):
    name = "SanBeniculturaliDownloader"

    def parse(self, response, **kwargs):
        # If is an image page, save it
        if 'jpg' in response.url.split('.'):
            yield ImageItem(image_urls=[response.xpath('//*[@id="zoomAntenati1"]').attrib['href']],
                            image_name=[response.url.replace('.jpg', '').replace(':', '').replace('.html', '')[41:]])
        else:
            # Iterate over the elements in the central page
            table = response.xpath('//*[@id="gsThumbMatrix"]//a')
            for elem in table:
                yield response.follow(elem.attrib['href'], callback=self.parse)

            # Check if there are multiple pages
            # Controlla se ci sono più pagine
            next_page = response.css('div.next-and-last a::attr(href)').get()
            if next_page is not None:
                yield response.follow(next_page, callback=self.parse)
