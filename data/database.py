import sqlite3
from sqlite3 import Error
from threading import Thread
import queue
import time


# class for manage all database operations
class Database:                

    def __init__(self):
        # queue of dict {k, v} where k is the url to add, v is the prefix, both string
        self.urls_to_add = queue.Queue()
        self.setting_to_add = queue.Queue()
        self.appoggio = queue.Queue()
        self.wait = False
        self.run = True
        self._thread = MyThread(parent=self)
        self._thread.start()

    def get_setting(self, key):
        return self._add_request(key, 'setting')

    def get_url(self, key):
        return self._add_request(key, 'url')

    def get_url_list(self, key):
        return self._add_request(key, 'url_list')

    def _add_request(self, key, tabella):
        # send request
        while True:
            time.sleep(0.5)
            if not self._thread.lock and self.appoggio.empty():
                self._thread.lock = True
                self._thread.tmp_tabella = tabella
                self.appoggio.put(key)
                self.wait = True
                break
        # wait response
        while True:
            time.sleep(0.5)
            if not self.wait and not self.appoggio.empty():
                v = self.appoggio.get()
                self._thread.lock = False
                return v

    def get_callbak(self):
        self.wait = False


    
    
class MyThread(Thread):
    url_table_query = """CREATE TABLE IF NOT EXISTS URL (
                                    id integer PRIMARY KEY,
                                    url text NOT NULL,
                                    prefix integer,
                                    FOREIGN KEY(prefix) REFERENCES URL(id)
                                ); """
    cartella_digitale_table_query = """CREATE TABLE IF NOT EXISTS CARTELLA_DIGITALE (
                                        numero text PRIMARY KEY
                                    ); """
    immagine_table_query = """CREATE TABLE IF NOT EXISTS IMMAGINE (
                                        id integer PRIMARY KEY,
                                        url integer NOT NULL,
                                        cartella_digitale integer NOT NULL,
                                        numero text NOT NULL,
                                        download_url int NOT NULL UNIQUE,
                                        FOREIGN KEY(url) REFERENCES URL(id),
                                        FOREIGN KEY(download_url) REFERENCES URL(id),
                                        FOREIGN KEY(cartella_digitale) REFERENCES CARTELLA_DIGITALE(id)
                                    ); """
    setting_table_query = """CREATE TABLE IF NOT EXISTS SETTING (
                                        id integer PRIMARY KEY,
                                        key text UNIQUE NOT NULL,
                                        value text
                                    ); """
    tmp_tabella = ''
    lock = False

    def __init__(self, parent=None):
        self._parent = parent

        # check if database exist, open or create it
        self._db_file = "C:\\Users\\cuoghmar\\Desktop\\images\\data.db"

        super(MyThread, self).__init__()

    def run(self):
        self._create_connection()
        
        # check if all tables exist
        self._execute_query(self.url_table_query)
        self._execute_query(self.cartella_digitale_table_query)
        self._execute_query(self.immagine_table_query)
        self._execute_query(self.setting_table_query)
        self._conn.commit()

        while self._parent.run:
            time.sleep(0.05)
            # check if url to add
            if not self._parent.urls_to_add.empty():
                k, v = next(iter(self._parent.urls_to_add.get().items()))
                self._add_url_database(k, v)
            # check if setting to add
            if not self._parent.setting_to_add.empty():
                k, v = next(iter(self._parent.setting_to_add.get().items()))
                self._add_setting_database(k, v)
            # check if setting to get
            if self.lock and not self._parent.appoggio.empty() and self._parent.wait:
                k = self._parent.appoggio.get()
                self._get_from_table(k)
                self._parent.get_callbak()

    def _execute_query(self, sql_query, data=None):
        """ execute the given query """
        try:
            if data:
                self._cur.execute(sql_query, data)
            else:
                self._cur.execute(sql_query)
        except Error as e:
            print(e)

    def _add_setting_database(self, key, value):
        # check if setting already exist
        setting_val = self._get_setting(key)
        if setting_val == str(value):
            # value same as the one in the database
            return
        if setting_val:
            # update value
            query = "UPDATE SETTING set value = '{}' where key = '{}'".format(value, key)
            self._execute_query(query)
        else:
            # insert key, value
            query = "INSERT INTO SETTING(key, value) VALUES(?,?)"
            self._execute_query(query, (key, value))
        self._conn.commit()

    def _add_url_database(self, url, prefix):
        # check if prefix exist
        id_prefix = self._get_url_id(prefix)
        if prefix and not id_prefix:
            # the prefix is not yet in db, re add to the queue for future add
            self._parent.urls_to_add.put({url: prefix})
            return
        # check if given url is already in db
        if self._get_url_id(url):
            return
        # add url to db
        query = "INSERT INTO URL(url, prefix) VALUES(?,?)"
        self._execute_query(query, (url, id_prefix))
        self._conn.commit()

    def _create_connection(self):
        """ create a database connection to a SQLite database """
        self._conn = None
        try:
            self._conn = sqlite3.connect(self._db_file)
            self._cur = self._conn.cursor()
        except Error as e:
            print(e)

    def _get_url_id(self, url):
        query = "SELECT id FROM URL WHERE url = '{}'".format(url)
        self._execute_query(query)
        tmp = self._cur.fetchone()
        if tmp:
            return tmp[0]
        return None
    
    def _get_url_row(self, id):
        query = "SELECT * FROM URL WHERE id = '{}'".format(id)
        self._execute_query(query)
        tmp = self._cur.fetchone()
        if tmp:
            return tmp
        return None

    def _get_url_child(self, key):
        query = "SELECT * FROM URL WHERE prefix = '{}'".format(key)
        self._execute_query(query)
        tmp = self._cur.fetchall()
        if tmp:
            return tmp
        return None

    def _get_setting(self, key):
        query = "SELECT value FROM SETTING WHERE key = '{}'".format(key)
        self._execute_query(query)
        tmp = self._cur.fetchone()
        if tmp:
            return tmp[0]
        return None

    def _get_from_table(self, k):
        if self.tmp_tabella == 'setting':
            v = self._get_setting(k)
            if v:
                self._parent.appoggio.put(v)
            else:
                self._parent.appoggio.put("")
        elif self.tmp_tabella == 'url':
            v = self._get_full_url(k)
            v2 = list(self._get_url_row(k))
            if v2:
                retval = [v2[0], v, v2[2]]
                self._parent.appoggio.put(retval)
            else:
                self._parent.appoggio.put("")
        elif self.tmp_tabella == 'url_list':
            v = self._get_url_child(k)
            if v:
                self._parent.appoggio.put(v)
            else:
                self._parent.appoggio.put([])

    def _get_full_url(self, id):
        tmp = self._get_url_row(id)
        if not tmp:
            return ""
        prefix = tmp[2]
        url = tmp[1]
        return self._get_full_url(prefix) + "/" + url
