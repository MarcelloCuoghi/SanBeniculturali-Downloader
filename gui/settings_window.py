import time
from data.database import Database
import queue
import requests
from lxml import html
from threading import Thread
from lxml.cssselect import CSSSelector
from tkinter import *
from tkinter.ttk import *


class MainWindow:
    db = None

    def __init__(self):
        self.root = Tk()
        self.root.wm_title("Settings")

        self.mainframe = Frame(self.root, padding="3 3 12 12")
        self.mainframe.grid(column=0, row=0, sticky=("nsew"))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # feet = StringVar()
        # feet_entry = Entry(self.mainframe, width=7, textvariable=feet)
        # feet_entry.grid(column=2, row=1, sticky=("nsew"))
        # self.mainframe.columnconfigure(2, weight=1)
        # self.mainframe.rowconfigure(1, weight=1)

        # create the upper row of buttons
        self.buttonframe = Frame(self.mainframe)
        self.buttonframe.grid(row=0, column=0, columnspan=2)
        Button(self.buttonframe, text="Back", command=self.back_click).grid(row=0, column=0)
        Button(self.buttonframe, text="Refresh", command=self.update_list).grid(row=0, column=2)
        Button(self.buttonframe, text="Settings").grid(row=0, column=3)

        # label of current position
        self.list_title = Label(self.mainframe)
        self.list_title.grid(row=1, column=0)
        # create the list of objects
        self.object_list = Listbox(self.mainframe, selectmode=SINGLE)
        self.object_list.grid(row=2, column=0, sticky="nsew")
        self.mainframe.rowconfigure(2, weight=1)
        self.mainframe.columnconfigure(0, weight=1)
        self.object_list.bind('<Double-1>', lambda e: self.click_on_element(e))
        # add scrollbar
        self.scrollbar = Scrollbar(self.mainframe, orient=VERTICAL)
        # self.scrollbar.rowconfigure(0, weight=1)
        # self.scrollbar.columnconfigure(0, weight=1)
        self.object_list.config(yscrollcommand=self.scrollbar.set)
        self.scrollbar.config(command=self.object_list.yview)
        self.scrollbar.grid(row=2, column=1, sticky=NS)

        # add progress bar
        self.progress = Progressbar(self.mainframe, orient=HORIZONTAL, length=100, mode='indeterminate')

        # create the lower row of buttons
        self.button_frame2 = Frame(self.mainframe)
        self.button_frame2.grid(row=3, column=0, columnspan=2)
        Button(self.button_frame2, text="Open Folder").grid(row=0, column=0)
        Button(self.button_frame2, text="Download").grid(row=0, column=1)

        self.history = queue.Queue()
        self.current_url = "/gallery"
        self.end_init()

    def end_init(self):
        self.root.after(10, self.update_list)
        self.root.mainloop()

    def update_list(self):
        thread = Thread(target=update_list_background, args=(self,))
        thread.start()
        self.loading_bar()

    def click_on_element(self, event):
        cs = self.object_list.curselection()
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

    # Function responsible for the updating of the progress bar value
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
        self.object_list.delete(0, END)
        i = 0
        for child in self.url_list:
            self.object_list.insert(i, child.split('/')[-2])
            i += 1

    def __del__(self):
        if self.db:
            self.db.run = False
