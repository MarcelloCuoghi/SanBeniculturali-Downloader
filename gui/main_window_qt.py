"""Main windows"""
import os
import platform
import subprocess
from threading import Thread
from multiprocessing import Process
import webbrowser

from PyQt5.QtCore import Qt
from lxml import html
import requests
from scrapy.utils.project import get_project_settings
from scrapy.crawler import CrawlerRunner
from twisted.internet import reactor
from data.spiders.spiders import SanBeniculturaliDownloader
from .waitingspinnerwidget import QtWaitingSpinner
from PyQt5 import QtWidgets, uic, QtCore


BASE_URL = 'http://dl.antenati.san.beniculturali.it'


def update_list_background(caller):
    """Start the download of urls in background"""
    caller.url_list = []
    get_url_list(caller.current_url, caller.url_list)

    caller.qt_current_position.setText(caller.current_url)
    caller.qt_url_list.clear()
    for i in caller.url_list:
        if 'jpg' in i:
            caller.qt_url_list.addItem(i.split('/')[-1])
        else:
            caller.qt_url_list.addItem(i.split('/')[-2])
    caller.qt_url_list.setCurrentRow(0)
    caller.spinner.stop()


def get_url_list(url, urls):
    """get all the urls in the page"""
    req = requests.get(BASE_URL + url)
    tree = html.fromstring(req.content)

    table = tree.xpath('//*[@id="gsThumbMatrix"]//a')
    for elem in table:
        if elem.attrib['href'] not in urls:
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
    msg_box = QtWidgets.QMessageBox()

    msg_box.setTextFormat(QtCore.Qt.RichText)
    msg_box.setWindowTitle("About")
    msg_box.setText("SanBeniculturali Downloader<br>Created by <a href='https://github.com/MarcelloCuoghi'>Marcello "
                    "Cuoghi</a>")
    msg_box.exec()


def open_guide():
    """Open the release page on GitHub"""
    url = 'https://github.com/MarcelloCuoghi/SanBeniculturali-Downloader/releases'
    webbrowser.open(url, new=2)


class MainWindowQt(QtWidgets.QMainWindow):
    """Main windows class"""
    url_list = []
    downloading_list = []
    current_url = "/gallery/"
    selected_item_download = ""
    selected_item = ""

    def __init__(self):
        """Constructor"""
        super().__init__()  # Call the inherited classes __init__ method
        uic.loadUi('.\\gui\\resources\\mainwindow.ui', self)  # Load the .ui file
        self.show()  # Show the GUI

        self.qt_url_list = self.findChild(QtWidgets.QListWidget, 'UrlList')
        self.qt_url_list.itemDoubleClicked.connect(self.click_on_element)
        self.qt_url_list.itemSelectionChanged.connect(self.change_selection)

        self.qt_back_btn = self.findChild(QtWidgets.QPushButton, 'BackButton')
        self.qt_back_btn.clicked.connect(self.back_click)

        self.qt_refresh_btn = self.findChild(QtWidgets.QPushButton, 'RefreshButton')
        self.qt_refresh_btn.clicked.connect(self.update_list)

        self.qt_goto_top_btn = self.findChild(QtWidgets.QPushButton, 'GotoUrl')
        self.qt_goto_top_btn.clicked.connect(self.open_in_browser_from_label)

        self.qt_folder_btn = self.findChild(QtWidgets.QPushButton, 'FolderButton')
        self.qt_folder_btn.clicked.connect(open_folder)

        self.qt_download_btn = self.findChild(QtWidgets.QPushButton, 'DownloadButton')
        self.qt_download_btn.clicked.connect(self.download)

        self.qt_goto_bottom_btn = self.findChild(QtWidgets.QPushButton, 'GotoButton')
        self.qt_goto_bottom_btn.clicked.connect(self.open_in_browser)

        self.qt_current_position = self.findChild(QtWidgets.QLabel, 'UrlOpen')

        qt_reset = self.findChild(QtWidgets.QAction, 'actionReset')
        qt_reset.triggered.connect(self.reset)
        qt_exit = self.findChild(QtWidgets.QAction, 'actionExit')
        qt_exit.triggered.connect(self.close)
        qt_guide = self.findChild(QtWidgets.QAction, 'actionGuide')
        qt_guide.triggered.connect(open_guide)
        qt_about = self.findChild(QtWidgets.QAction, 'actionAbout')
        qt_about.triggered.connect(about)

        self.spinner = QtWaitingSpinner(self, disableParentWhenSpinning=True)
        self.spinner.setRoundness(70.0)
        self.spinner.setMinimumTrailOpacity(15.0)
        self.spinner.setTrailFadePercentage(70.0)
        self.spinner.setNumberOfLines(12)
        self.spinner.setLineLength(10)
        self.spinner.setLineWidth(5)
        self.spinner.setInnerRadius(10)
        self.spinner.setRevolutionsPerSecond(1)

        self.qt_downloading_list = self.findChild(QtWidgets.QListWidget, 'downloadingList')
        self.qt_downloading_list.itemSelectionChanged.connect(self.change_selection_download)
        self.qt_stop_btn = self.findChild(QtWidgets.QPushButton, 'stopButton')
        self.qt_stop_btn.clicked.connect(self.stop)

        self.update_list()

    def keyPressEvent(self, event):
        """Event on key press"""
        if event.key() == Qt.Key_Return:
            self.click_on_element()
        elif event.key() == Qt.Key_Backspace:
            self.back_click()

    def mousePressEvent(self, event):
        """Event on mouse key press"""
        if event.buttons() == Qt.BackButton:
            self.back_click()

    def update_list(self):
        """Update the list of url in background"""
        self.spinner.start()
        thread = Thread(target=update_list_background, args=(self,))
        thread.start()

    def back_click(self):
        """Action on click on back button"""
        tmp = '/' + '/'.join(list(filter(None, self.current_url.split("/")))[:-1])
        if tmp != "/":
            if tmp != '/v':
                self.current_url = tmp
            else:
                self.current_url = "/gallery"
            self.update_list()

    def open_in_browser_from_label(self):
        """Open web browser of current item"""
        webbrowser.open(BASE_URL + self.current_url, new=2)

    def open_in_browser(self):
        """Open web browser of selected item"""
        if self.selected_item:
            webbrowser.open(BASE_URL + self.selected_item, new=2)

    def reset(self):
        """Return to the starting point"""
        self.current_url = "/gallery"
        self.update_list()

    def click_on_element(self, item=None):
        """Action on click on element"""
        if item:
            self.change_selection(item)
        if self.selected_item:
            if 'jpg' in self.selected_item:
                self.open_in_browser()
                return
            self.current_url = self.selected_item
        self.update_list()
        self.selected_item = ""

    def change_selection(self, item=None):
        """Find the given item"""
        if not item:
            item = self.qt_url_list.currentItem()
        self.selected_item = ""
        for i in range(len(self.url_list)):
            if 'jpg' in item.text() and self.url_list[i].split("/")[-1] == item.text():
                self.selected_item = self.url_list[i]
                return
            elif self.url_list[i].split("/")[-2] == item.text():
                self.selected_item = self.url_list[i]
                return

    def change_selection_download(self, item=None):
        """Find the given item"""
        if not item:
            item = self.qt_url_list.currentItem()
        self.selected_item_download = ""
        for i in range(len(self.url_list)):
            if 'jpg' in item.text() and self.url_list[i].split("/")[-1] == item.text():
                self.selected_item_download = self.url_list[i]
                return
            elif self.url_list[i].split("/")[-2] == item.text():
                self.selected_item_download = self.url_list[i]
                return

    def download(self):
        """Request for confirmation about downloading current item"""
        if self.selected_item:
            item_name = "..." + self.selected_item[len(self.selected_item)-35:-1]
            msg_box = QtWidgets.QMessageBox()

            msg_box.setTextFormat(QtCore.Qt.RichText)
            msg_box.setIcon(QtWidgets.QMessageBox.Warning)
            msg_box.setWindowTitle("Download | Warning")
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            msg_box.setText(
                "Selected item: <a href='{}'>{}</a><br>Are you sure to download it?<br>The size of the download could "
                "be too much!".format(BASE_URL + self.selected_item, item_name))
            return_value = msg_box.exec()

            if return_value == QtWidgets.QMessageBox.Yes:
                self.start_download()

    def start_download(self):
        """Download current selected items"""
        self.downloading_list.append(self.selected_item)
        self.qt_downloading_list.addItem(self.selected_item)
        downloader = Process(target=scrapy_downloader, args=(self.selected_item,))
        downloader.start()

    def closeEvent(self, event):
        """Closing event, request if close or not the application"""
        reply = QtWidgets.QMessageBox.question(self, 'Window Close', 'Are you sure you want to close the application?',
                                   QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)

        if reply == QtWidgets.QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()

    def stop(self):
        """Stop the selected downloading"""
        if not self.selected_item_download:
            return
