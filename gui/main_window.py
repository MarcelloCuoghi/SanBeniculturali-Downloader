import time
import os
from data.database import Database
import queue
import requests
from lxml import html
from threading import Thread
from lxml.cssselect import CSSSelector
from tkinter import *
from tkinter.ttk import *
from tkinter import messagebox


BASE_URL = 'http://dl.antenati.san.beniculturali.it'


def update_list_background(caller):
        caller.url_list = []
        get_url_list(caller.current_url, caller.url_list)

        caller.loading = False


# get all the urls in the page
def get_url_list(url, urls):
        r = requests.get(BASE_URL + url)
        tree = html.fromstring(r.content)
        
        table = tree.xpath('//*[@id="gsThumbMatrix"]//a')
        for elem in table:
            urls.append(elem.attrib['href'])
        
        next_page = tree.cssselect('div.next-and-last')  
        if next_page and len(next_page) > 0 and len(next_page[0]):
            get_url_list(tree.cssselect('div.next-and-last')[0][0].get('href'), urls)


# download the request registry from the given url
def scrapy_downloader(url):
    import scrapy
    from scrapy.utils.project import get_project_settings
    from scrapy.crawler import CrawlerRunner
    from data.spiders.spiders import SanBeniculturaliDownloader
    from twisted.internet import reactor
    settings_file_path = 'data.settings' # The path seen from root, ie. from main.py
    os.environ.setdefault('SCRAPY_SETTINGS_MODULE', settings_file_path)

    settings = get_project_settings()
    path = os.getcwd()
    path += "\Download"
    if not os.path.exists(path):
        os.makedirs(path)
    settings['IMAGES_STORE'] = path
    runner = CrawlerRunner(settings)
    d = runner.crawl(SanBeniculturaliDownloader, start_urls = [BASE_URL + url, ])
    d.addBoth(lambda _: reactor.stop())
    reactor.run()


class MainWindow():
    db = None

    def __init__(self):
        self.root = Tk()
        self.root.wm_title("SanBeniculturali Downloader")
        self.root.geometry("1280x720")

        self.mainframe = Frame(self.root, padding="3 3 12 12")
        self.mainframe.grid(column=0, row=0, sticky=("nsew"))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # create the upper row of buttons
        self.buttonframe = Frame(self.mainframe)
        self.buttonframe.grid(row=0, column=0, columnspan=2)
        Button(self.buttonframe, text = "Back", command=self.back_click).grid(row=0, column=0)
        Button(self.buttonframe, text = "Refresh", command=self.update_list).grid(row=0, column=2)
        #Button(self.buttonframe, text = "Settings").grid(row=0, column=3)

        # label of current position
        self.list_title = Label(self.mainframe)
        self.list_title.grid(row=1, column=0)
        # create the list of objects
        self.objectlist = Listbox(self.mainframe, selectmode=SINGLE)
        self.objectlist.grid(row=2, column=0, sticky=("nsew"))
        self.mainframe.rowconfigure(2, weight=1)
        self.mainframe.columnconfigure(0, weight=1)
        self.objectlist.bind('<Double-1>', lambda e: self.click_on_element(e))
        # add scrollbar
        self.scrollbar = Scrollbar(self.mainframe, orient=VERTICAL)
        #self.scrollbar.rowconfigure(0, weight=1)
        #self.scrollbar.columnconfigure(0, weight=1)
        self.objectlist.config(yscrollcommand=self.scrollbar.set)
        self.scrollbar.config(command=self.objectlist.yview)
        self.scrollbar.grid(row=2, column=1, sticky=NS)

        # add progress bar
        self.progress = Progressbar(self.mainframe, orient = HORIZONTAL, length = 200, mode = 'indeterminate')
                
        # create the lower row of buttons
        self.buttonframe2 = Frame(self.mainframe)
        self.buttonframe2.grid(row=3, column=0, columnspan=2)
        Button(self.buttonframe2, text = "Open Folder", command=self.open_folder).grid(row=0, column=0)
        Button(self.buttonframe2, text = "Download", command=self.download).grid(row=0, column=1)

        self.history = queue.Queue()
        self.current_url = "/gallery"
        self.end_init()

    def end_init(self):
        self.root.after(200, self.update_list)
        self.root.mainloop()

    def update_list(self):
        thread = Thread(target=update_list_background, args=(self, ))        
        thread.start()
        self.loading_bar()

    def click_on_element(self, event):
        cs = self.objectlist.curselection() 
        if cs and len(cs) > 0:
            self.history.put(self.current_url)
            self.current_url = self.url_list[cs[0]]

        self.update_list()

    def back_click(self):
        tmp = '/' + '/'.join(list(filter(None, self.current_url.split("/")))[:-1]) 
        if tmp != "/":
            if tmp != '/v':
                self.current_url = tmp
            else:
                self.current_url = "/gallery"
            self.update_list()

    # Function responsible for the updation 
    # of the progress bar value 
    def loading_bar(self):
        import time 
        self.progress.grid(row=4, column=0)
        self.loading = True
        while self.loading:
            for i in range(0, 100, 10):
                self.progress['value'] = i
                self.root.update_idletasks() 
                time.sleep(0.1) 
            for i in range(100, 0, -10):
                self.progress['value'] = i
                self.root.update_idletasks() 
                time.sleep(0.1) 

        self.progress.grid_forget()
        self.root.update_idletasks() 
        self.list_title.config(text=self.current_url)
        self.objectlist.delete(0, END)
        i = 0
        for child in self.url_list:
                self.objectlist.insert(i, child.split('/')[-2])
                i+=1

    def open_folder(self):
        import os
        import platform
        import subprocess
        path = os.getcwd()
        path += "\Download"
        if not os.path.exists(path):
            os.makedirs(path)
        if platform.system() == "Windows":            
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])

    def download(self):
        cs = self.objectlist.curselection() 
        if cs and len(cs) > 0:
            selected = self.url_list[cs[0]]
            response = messagebox.askquestion(title="Download", message="Elemento selezionato: {}\nSei sicuro di volerlo scaricare? La dimensione del download potrebbe essere eccessiva".format(selected))
            if response == 'yes':
                self.start_download(selected)

    def start_download(self, url):
        from multiprocessing import Process
        p = Process(target=scrapy_downloader, args=(url, ))
        p.start()

    def __del__(self):
        if self.db:
            self.db.run = False
