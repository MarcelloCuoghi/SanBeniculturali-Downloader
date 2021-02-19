"""Item models used by scrapy"""
import scrapy


class ImageItem(scrapy.Item):  # pylint: disable=too-many-ancestors
    """Item used by scrapy for the full size image"""
    image_urls = scrapy.Field()
    images = scrapy.Field()
    image_name = scrapy.Field()
