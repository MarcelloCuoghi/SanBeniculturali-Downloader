"""Main windows"""
import os
import platform
import subprocess
from threading import Thread
import webbrowser

from PyQt5.QtCore import Qt
from PyQt5 import QtWidgets, uic, QtCore, QtTest
from data.downloader import BASE_URL, get_url_list, ImageDownloader
from .waitingspinnerwidget import QtWaitingSpinner


def update_list_background(caller):
    """Start the download of urls in background"""
    caller.url_list = []
    get_url_list(caller.current_url, caller.url_list)
    caller.end_loading = True


def open_folder():
    """Open download folder"""
    path = os.getcwd()
    if platform.system() == "Windows":
        path += "\\Download"
        if not os.path.exists(path):
            os.makedirs(path)
        os.startfile(path)
    elif platform.system() == "Linux":
        path += "/Download"
        if not os.path.exists(path):
            os.makedirs(path)
        subprocess.Popen(["xdg-open", path])
    elif platform.system() == 'Darwin':
        path += "/Download"
        if not os.path.exists(path):
            os.makedirs(path)
        subprocess.call(('open', path))


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
    current_url = ""
    selected_item_download = ""
    selected_item = ""
    lock = False
    end_loading = False

    def __init__(self):
        """Constructor"""
        super().__init__()  # Call the inherited classes __init__ method
        self.show()  # Show the GUI
        if platform.system() == 'Windows':
            uic.loadUi('.\\gui\\resources\\mainwindow.ui', self)
        elif platform.system() == 'Linux':
            uic.loadUi('./gui/resources/mainwindow.ui', self)
        elif platform.system() == 'Darwin':
            uic.loadUi('./gui/resources/mainwindow.ui', self)

        self.qt_url_list = self.findChild(QtWidgets.QListWidget, 'UrlList')
        self.qt_url_list.itemDoubleClicked.connect(self.click_on_element)
        self.qt_url_list.itemSelectionChanged.connect(self.change_selection)

        self.qt_downloading_list = self.findChild(QtWidgets.QListWidget, 'downloadingList')
        self.qt_downloading_list.itemSelectionChanged.connect(self.change_selection_download)
        self.qt_stop_btn = self.findChild(QtWidgets.QPushButton, 'stopButton')
        self.qt_stop_btn.clicked.connect(self.stop)

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

        self.spinner_down = QtWaitingSpinner(self.qt_downloading_list, disableParentWhenSpinning=True)
        self.spinner_down.setRoundness(70.0)
        self.spinner_down.setMinimumTrailOpacity(15.0)
        self.spinner_down.setTrailFadePercentage(70.0)
        self.spinner_down.setNumberOfLines(12)
        self.spinner_down.setLineLength(10)
        self.spinner_down.setLineWidth(5)
        self.spinner_down.setInnerRadius(10)
        self.spinner_down.setRevolutionsPerSecond(1)

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
        self.end_loading = False
        self.qt_url_list.clear()
        thread = Thread(target=update_list_background, args=(self,))
        thread.start()
        while not self.end_loading:
            QtTest.QTest.qWait(50)

        self.qt_current_position.setText(self.current_url)
        for i in self.url_list:
            if 'jpg' in i:
                self.qt_url_list.addItem(i.split('/')[-1])
            else:
                self.qt_url_list.addItem(i.split('/')[-2])
        self.spinner.stop()

    def back_click(self):
        """Action on click on back button"""
        tmp = '/' + '/'.join(list(filter(None, self.current_url.split("/")))[:-1])
        if tmp != "/":
            if tmp != '/v':
                self.current_url = tmp
            else:
                self.current_url = ""
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
        self.current_url = ""
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
        if not item:
            return
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
            item = self.qt_downloading_list.currentItem()
        if not item:
            return
        self.selected_item_download = ""
        for i in range(len(self.downloading_list)):
            if self.downloading_list[i].start_url == item.text():
                self.selected_item_download = self.downloading_list[i]
                return

    def download(self):
        """Request for confirmation about downloading current item"""
        if self.selected_item:
            if len(self.selected_item) - 35 < 0:
                item_name = "..." + self.selected_item[0:-1]
            else:
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
        tmp = ImageDownloader(self.selected_item)
        tmp.complete.connect(self.end_download)
        self.qt_downloading_list.addItem(self.selected_item)
        self.downloading_list.append(tmp)
        tmp.start()

    def closeEvent(self, event):
        """Closing event, request if close or not the application"""
        reply = QtWidgets.QMessageBox.question(self, 'Window Close', 'Are you sure you want to close the application?',
                                   QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)

        if reply == QtWidgets.QMessageBox.Yes:
            for elem in self.downloading_list:
                elem.stop()
            event.accept()
        else:
            event.ignore()

    def stop(self):
        """Stop the selected downloading"""
        self.spinner_down.start()
        if self.selected_item_download:
            self.spinner_down.start()
            self.selected_item_download.stop()
            self.selected_item_download = None

    def end_download(self, msg):
        """Notify end of the download"""
        info_msg = msg.split('|')[0]
        url = msg.split('|')[1]
        msg_box = QtWidgets.QMessageBox()
        msg_box.setWindowTitle("Download complete")
        msg_box.setText(info_msg + "\n{}".format(url))
        msg_box.exec()

        for i in range(len(self.downloading_list)):
            if self.downloading_list[i].start_url == url:
                self.downloading_list.remove(self.downloading_list[i])
                self.qt_downloading_list.takeItem(i)
                break

        self.spinner_down.stop()
