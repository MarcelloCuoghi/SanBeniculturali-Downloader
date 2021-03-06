"""Main starting script"""
import sys
from PyQt5 import QtWidgets
from gui.main_window_qt import MainWindowQt


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)  # Create an instance of QtWidgets.QApplication
    window = MainWindowQt()  # Create an instance of our class
    app.exec_()  # Start the application
