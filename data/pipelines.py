# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface
from itemadapter import ItemAdapter
from data.items import ImageItem
from scrapy.pipelines.images import ImagesPipeline
from scrapy.http import Request
import re
from data.database import Database


class MyImagesPipeline(ImagesPipeline):
    
    CONVERTED_ORIGINAL = re.compile('^full/[0-9,a-f]+.jpg$')

    # name information coming from the spider, in each item
    # add this information to Requests() for individual images downloads
    # through "meta" dictionary
    def get_media_requests(self, item, info):
        print("get_media_requests")
        return [Request(x, meta={'image_name': item["image_name"]})
                for x in item.get('image_urls', [])]

    # this is where the image is extracted from the HTTP response
    def get_images(self, response, request, info):
        print("get_images")
        for key, image, buf, in super(MyImagesPipeline, self).get_images(response, request, info):
            if self.CONVERTED_ORIGINAL.match(key):
                key = self.change_filename(key, response)
            yield key, image, buf

    def change_filename(self, key, response):
        return "%s.jpg" % response.meta['image_name'][0]


#class ItemCollectorPipeline(object):
#    """ Manage the url items"""

#    def __init__(self):
#        self.ids_seen = set()
#        self.db = Database()
#        self.urls = {}

#    def process_item(self, item, spider):
#        urls = list(filter(None, item['url'].split('/')))
#        previous_id = None
#        self.db.urls_to_add.put({urls[0]: ""})
#        for i in range(1, len(urls)):
#            self.db.urls_to_add.put({urls[i]: urls[i - 1]})
