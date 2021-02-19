""" Define your item pipelines here

Don't forget to add your pipeline to the ITEM_PIPELINES setting
See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html
"""

import re
from scrapy.pipelines.images import ImagesPipeline
from scrapy.http import Request


def change_filename(response):
    """Change file name to the same as the URL"""
    return "%s.jpg" % response.meta['image_name'][0]


class MyImagesPipeline(ImagesPipeline):
    """Pipeline used by scrapy do download the image"""
    CONVERTED_ORIGINAL = re.compile('^full/[0-9,a-f]+.jpg$')

    def file_path(self, request, response=None, info=None, *, item=None):
        """Name file as in the URL"""
        return "%s.jpg" % response.meta['image_name'][0]

    # name information coming from the spider, in each item
    # add this information to Requests() for individual images downloads
    # through "meta" dictionary
    def get_media_requests(self, item, info):
        return [Request(x, meta={'image_name': item["image_name"]})
                for x in item.get('image_urls', [])]

    def get_images(self, response, request, info, **kwargs):
        for key, image, buf, in super().get_images(response, request, info):
            if self.CONVERTED_ORIGINAL.match(key):
                key = change_filename(response)
            yield key, image, buf

# class ItemCollectorPipeline(object):
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
