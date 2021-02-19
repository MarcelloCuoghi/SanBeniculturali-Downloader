"""Main windows"""
import os
import platform
import subprocess
import multiprocessing
import time
from threading import Thread
from multiprocessing import Process
import webbrowser
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from lxml import html
import requests
from PIL import Image
from PIL import ImageTk
from scrapy.utils.project import get_project_settings
from scrapy.crawler import CrawlerRunner
from twisted.internet import reactor
from data.spiders.spiders import SanBeniculturaliDownloader

BASE_URL = 'http://dl.antenati.san.beniculturali.it'


def update_list_background(caller):
    """Start the download of urls in background"""
    caller.url_list = []
    get_url_list(caller.current_url, caller.url_list)

    caller.loading = False


def get_url_list(url, urls):
    """get all the urls in the page"""
    req = requests.get(BASE_URL + url)
    tree = html.fromstring(req.content)

    table = tree.xpath('//*[@id="gsThumbMatrix"]//a')
    for elem in table:
        urls.append(elem.attrib['href'])

    next_page = tree.cssselect('div.next-and-last')
    if next_page and len(next_page) and len(next_page[0]):
        get_url_list(tree.cssselect('div.next-and-last')[0][0].get('href'), urls)


def scrapy_downloader(url):
    """download the request registry from the given url"""
    settings_file_path = 'data.settings'  # The path seen from root, ie. from main.py
    os.environ.setdefault('SCRAPY_SETTINGS_MODULE', settings_file_path)

    settings = get_project_settings()
    path = os.getcwd()
    path += "\\Download"
    if not os.path.exists(path):
        os.makedirs(path)
    settings['IMAGES_STORE'] = path
    runner = CrawlerRunner(settings)
    crawler = runner.crawl(SanBeniculturaliDownloader, start_urls=[BASE_URL + url, ])
    crawler.addBoth(lambda _: reactor.stop())
    reactor.run()


def open_folder():
    """Open download folder"""
    path = os.getcwd()
    path += "\\Download"
    if not os.path.exists(path):
        os.makedirs(path)
    if platform.system() == "Windows":
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", path])


def about():
    """Show author info"""
    tk.messagebox.showinfo(title="About",
                           message="""SanBeniculturali Downloader
Creato da Marcello Cuoghi
https://github.com/MarcelloCuoghi""")



class MainWindow:
    """Main windows class"""
    # pylint: disable=too-many-instance-attributes

    db = None
    loading = False
    url_list = []

    def __init__(self):
        """Constructor"""
        # pylint: disable=too-many-statements
        multiprocessing.freeze_support()
        self.root = tk.Tk()
        self.root.wm_title("SanBeniculturali Downloader")
        self.root.geometry("1280x720")

        self.menu_bar = tk.Menu(self.root)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.file_menu.add_command(label="Reset", command=self.reset)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.root.quit)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu)
        self.help_menu = tk.Menu(self.menu_bar, tearoff=0)
        self.help_menu.add_command(label="Help")
        self.help_menu.add_command(label="About...", command=about)
        self.menu_bar.add_cascade(label="Help", menu=self.help_menu)
        self.root.config(menu=self.menu_bar)

        self.mainframe = tk.Frame(self.root)
        self.mainframe.grid(column=0, row=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # create the upper row of buttons
        self.button_frame = tk.Frame(self.mainframe)
        self.button_frame.grid(row=0, column=0, columnspan=2)
        path = os.getcwd()

        img1 = Image.open(path + "\\gui\\resources\\back.png")
        img1 = img1.resize((25, 25), Image.ANTIALIAS)
        photo_img1 = ImageTk.PhotoImage(img1)
        tk.Button(self.button_frame,
                  text="Back",
                  image=photo_img1,
                  command=self.back_click).grid(row=0, column=0,
                                                padx=5,
                                                pady=10)
        img2 = Image.open(path + "\\gui\\resources\\refresh.png")
        img2 = img2.resize((25, 25), Image.ANTIALIAS)
        photo_img2 = ImageTk.PhotoImage(img2)
        tk.Button(self.button_frame,
                  text="Refresh",
                  image=photo_img2,
                  command=self.update_list).grid(row=0, column=2,
                                                 padx=5, pady=10)

        # label of current position
        self.list_title = tk.Label(self.mainframe)
        self.list_title.grid(row=1, column=0, pady=10)

        # create the list of objects
        self.object_list = tk.Listbox(self.mainframe, selectmode=tk.SINGLE)
        self.object_list.grid(row=2, column=0, sticky="nsew")
        self.mainframe.rowconfigure(2, weight=1)
        self.mainframe.columnconfigure(0, weight=1)
        self.object_list.bind('<Double-1>', lambda e: self.click_on_element())
        # add scrollbar
        self.scrollbar = tk.Scrollbar(self.mainframe, orient=tk.VERTICAL)
        self.object_list.config(yscrollcommand=self.scrollbar.set)
        self.scrollbar.config(command=self.object_list.yview)
        self.scrollbar.grid(row=2, column=1, sticky=tk.NS)

        # create the lower row of buttons
        self.button_frame2 = tk.Frame(self.mainframe)
        self.button_frame2.grid(row=3, column=0, columnspan=2, pady=10)

        img3 = Image.open(path + "\\gui\\resources\\folder.png")
        img3 = img3.resize((25, 25), Image.ANTIALIAS)
        photo_img3 = ImageTk.PhotoImage(img3)
        tk.Button(self.button_frame2,
                  text="Open Download Folder",
                  image=photo_img3,
                  command=open_folder).grid(row=0,
                                            column=0,
                                            padx=5,
                                            pady=10)

        img4 = Image.open(path + "\\gui\\resources\\download.png")
        img4 = img4.resize((25, 25), Image.ANTIALIAS)
        photo_img4 = ImageTk.PhotoImage(img4)
        tk.Button(self.button_frame2,
                  text="Download",
                  image=photo_img4,
                  command=self.download).grid(row=0, column=1,
                                              padx=5,
                                              pady=10)

        img5 = Image.open(path + "\\gui\\resources\\web.png")
        img5 = img5.resize((25, 25), Image.ANTIALIAS)
        photo_img5 = ImageTk.PhotoImage(img5)
        tk.Button(self.button_frame2,
                  text="Open in Browser",
                  image=photo_img5,
                  command=self.open_in_browser).grid(row=0, column=2, padx=5, pady=10)
        tk.Button(self.mainframe, text="Open Browser",
                  image=photo_img5,
                  command=self.open_in_browser_from_label).grid(
            row=1, column=1, padx=5, pady=10)

        # add progress bar
        self.progress = ttk.Progressbar(self.button_frame2,
                                        orient=tk.HORIZONTAL,
                                        length=200,
                                        mode='determinate')
        self.progress.grid(row=0, column=3, pady=10)

        # list of downloading elements
        self.downloading_title = tk.Label(self.mainframe)
        self.downloading_title.grid(row=5, column=0, pady=10)
        self.downloading_title.config(text="In download")
        # create the list of objects
        self.downloading_list = tk.Listbox(self.mainframe, selectmode=tk.SINGLE)
        self.downloading_list.grid(row=6, column=0, sticky=tk.NSEW)
        self.mainframe.rowconfigure(6, weight=1)
        self.mainframe.columnconfigure(0, weight=1)
        # add scrollbar
        self.scrollbar2 = tk.Scrollbar(self.mainframe, orient=tk.VERTICAL)
        self.downloading_list.config(yscrollcommand=self.scrollbar2.set)
        self.scrollbar2.config(command=self.downloading_list.yview)
        self.scrollbar2.grid(row=6, column=1, sticky=tk.NS)

        self.current_url = "/gallery"
        self.end_init()

    def end_init(self):
        """End constructor, start the GUI"""
        self.root.after(200, self.update_list)
        self.root.mainloop()

    def update_list(self):
        """Update the list of url in background"""
        thread = Thread(target=update_list_background, args=(self,))
        thread.start()
        self.loading_bar()

    def click_on_element(self):
        """Action on click on element"""
        cur_sel = self.object_list.curselection()
        if cur_sel and len(cur_sel) > 0:
            if 'jpg' in self.url_list[cur_sel[0]]:
                self.open_in_browser()
                return
            self.current_url = self.url_list[cur_sel[0]]
        self.update_list()

    def back_click(self):
        """Action on click on back button"""
        tmp = '/' + '/'.join(list(filter(None, self.current_url.split("/")))[:-1])
        if tmp != "/":
            if tmp != '/v':
                self.current_url = tmp
            else:
                self.current_url = "/gallery"
            self.update_list()

    def loading_bar(self):
        """Method responsible for the updating of the progress bar value"""
        self.loading = True
        while self.loading:
            for i in range(0, 110, 10):
                if not self.loading:
                    break
                self.progress['value'] = i
                self.root.update_idletasks()
                time.sleep(0.1)

        # self.progress.grid_forget()
        self.progress['value'] = 100
        self.root.update_idletasks()
        self.list_title.config(text=self.current_url)
        self.object_list.delete(0, tk.END)
        i = 0
        for child in self.url_list:
            if 'jpg' in child:
                self.object_list.insert(i, child.split('/')[-1])
            else:
                self.object_list.insert(i, child.split('/')[-2])
            i += 1

    def download(self):
        """Request for confirmation about downloading current item"""
        cur_sel = self.object_list.curselection()
        if cur_sel and len(cur_sel) > 0:
            selected = self.url_list[cur_sel[0]]
            response = messagebox.askquestion(title="Download",
                                              message="""Elemento selezionato: {}
                                              Sei sicuro di volerlo scaricare? La dimensione del download potrebbe 
                                              essere eccessiva""".format(
                                                  selected))
            if response == 'yes':
                self.start_download(selected)

    def start_download(self, url):
        """Download current selected items"""
        i = self.downloading_list.size()
        self.downloading_list.insert(i, url)
        downloader = Process(target=scrapy_downloader, args=(url,))
        downloader.start()

    def open_in_browser_from_label(self):
        """Open web browser of current item"""
        webbrowser.open(BASE_URL + self.current_url, new=2)

    def open_in_browser(self):
        """Open web browser of selected item"""
        cur_sel = self.object_list.curselection()
        if cur_sel and len(cur_sel) > 0:
            selected = self.url_list[cur_sel[0]]
            webbrowser.open(BASE_URL + selected, new=2)

    def reset(self):
        """Return to the starting point"""
        self.current_url = "/gallery"
        self.update_list()

    def __del__(self):
        if self.db:
            self.db.run = False
