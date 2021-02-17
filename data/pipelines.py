# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface

from scrapy.pipelines.images import ImagesPipeline
from scrapy.http import Request
import re


def change_filename(response):
    return "%s.jpg" % response.meta['image_name'][0]


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
    def get_images(self, response, request, info, **kwargs):
        print("get_images")
        for key, image, buf, in super(MyImagesPipeline, self).get_images(response, request, info):
            if self.CONVERTED_ORIGINAL.match(key):
                key = change_filename(response)
            yield key, image, buf
