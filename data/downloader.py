import os
import time
from threading import Thread
from queue import Queue
import requests
from PyQt5 import QtCore
from lxml import html
import urllib
from datetime import datetime
from multiprocessing import Process


BASE_URL = 'http://dl.antenati.san.beniculturali.it'


def get_url_list(url, urls):
    """get all the urls in the page"""
    req = requests.get(BASE_URL + url)
    tree = html.fromstring(req.content)

    table = tree.xpath('//*[@class="giTitle"]//a')
    for elem in table:
        if elem.attrib['href'] not in urls:
            urls.append(elem.attrib['href'])

    next_page = tree.cssselect('div.next-and-last')
    if next_page and len(next_page) and len(next_page[0]):
        get_url_list(tree.cssselect('div.next-and-last')[0][0].get('href'), urls)


def get_url_list_download(url, urls, run):
    """get all the urls in the page"""
    if not run():
        return
    req = requests.get(BASE_URL + url)
    tree = html.fromstring(req.content)

    table = tree.xpath('//*[@class="giTitle"]//a')
    for elem in table:
        # Save url image
        if 'jpg' in elem.attrib['href']:
            urls.put(elem.attrib['href'])
        # Get urls in children
        else:
            get_url_list_download(elem.attrib['href'], urls, run)
    # Chek for multiple pages
    next_page = tree.cssselect('div.next-and-last')
    if next_page and len(next_page) and len(next_page[0]):
        get_url_list_download(tree.cssselect('div.next-and-last')[0][0].get('href'), urls, run)


def save_image(image):
    """Save image to local device"""
    path = os.getcwd() + "\\Download\\"
    for folder in image[3:].split('/')[:-1]:
        path += folder + "\\"
    if folder and not os.path.exists(path):
        os.makedirs(path)
    request = requests.get(BASE_URL + image)
    data_image = html.fromstring(request.content)
    img_src = data_image.xpath('//*[@id="gsImageView"]//a')[0].attrib.get('href')
    if img_src:
        urllib.request.urlretrieve(img_src, path + image.split('/')[-1][:-5])


def get_size(start_path='.'):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(start_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            # skip if it is symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size


class ImageDownloader(QtCore.QThread):
    """Class to manage the downloading of images"""
    url_list = Queue()
    complete = QtCore.pyqtSignal(object)
    start_time = None
    tot_download = 0
    processes = []

    def __init__(self, start_url):
        """Constructor"""
        QtCore.QThread.__init__(self)
        print("Created downloader for {}".format(start_url))
        self.start_url = start_url
        self.run = False
        self.get_urls = Thread(target=get_url_list_download, args=(self.start_url, self.url_list, lambda: self.run))
        self.download_images = Thread(target=self.download)

    def run(self):
        """Start the downloading of images"""
        self.run = True
        self.start_time = datetime.now()
        self.get_urls.start()
        self.download_images.start()

    def download(self):
        """Download the images from the urls list"""
        while self.get_urls.is_alive():
            if not self.url_list.empty():
                if len(self.processes) < 5:
                    image = self.url_list.get()
                    p = Process(target=save_image, args=(image,))
                    p.start()
                    self.processes.append(p)
                    self.tot_download += 1
                else:
                    for _ in range(len(self.processes)):
                        self.processes[0].join()
                        self.processes = self.processes[1:]
            time.sleep(0.005)
        if self.run:
            while not self.url_list.empty():
                if len(self.processes) < 5:
                    image = self.url_list.get()
                    p = Process(target=save_image, args=(image,))
                    p.start()
                    self.processes.append(p)
                    self.tot_download += 1
                else:
                    for _ in range(len(self.processes)):
                        self.processes[0].join()
                        self.processes = self.processes[1:]
        for p in self.processes:
            p.join()
        self.run = False

        path = os.getcwd() + "\\Download\\"
        for folder in self.start_url[3:].split('/')[:-1]:
            path += folder + "\\"
        size = get_size(path)
        msg = "End in {} download {} images, {:.5g} Mbytes".format(
            datetime.now()-self.start_time, self.tot_download, size/1024/1024)

        self.complete.emit(msg + '|' + self.start_url)

    def stop(self):
        """Stop the downloading of images"""
        self.run = False
