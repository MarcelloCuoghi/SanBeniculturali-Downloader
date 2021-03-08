"""Main starting script"""
import multiprocessing
import sys
import platform
from PyQt5 import QtWidgets
from gui.main_window_qt import MainWindowQt
import multiprocessing


if __name__ == "__main__":
    if platform.system() == 'Windows':
        multiprocessing.freeze_support()
    app = QtWidgets.QApplication(sys.argv)  # Create an instance of QtWidgets.QApplication
    window = MainWindowQt()  # Create an instance of our class
    app.exec_()  # Start the application
