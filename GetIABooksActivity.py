#! /usr/bin/env python

# Copyright (C) 2009 James D. Simmons
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
import os
import logging
import tempfile
import time
import pygtk
import gtk
import string
import csv
import urllib
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.menuitem import MenuItem
from sugar.graphics.toolcombobox import ToolComboBox
from sugar.graphics.combobox import ComboBox
from sugar import profile
from sugar.activity import activity
from sugar import network
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from gettext import gettext as _
import pango
import dbus
import gobject

_TOOLBAR_BOOKS = 1
COLUMN_CREATOR = 0
COLUMN_DESCRIPTION=1
COLUMN_FORMAT = 2
COLUMN_IDENTIFIER = 3
COLUMN_LANGUAGE = 4
COLUMN_PUBLICDATE= 5
COLUMN_PUBLISHER = 6
COLUMN_SUBJECT = 7
COLUMN_TITLE = 8
COLUMN_VOLUME = 9
COLUMN_TITLE_TRUNC = 10
COLUMN_CREATOR_TRUNC = 11

_logger = logging.getLogger('get-ia-books-activity')

class BooksToolbar(gtk.Toolbar):
    __gtype_name__ = 'BooksToolbar'

    def __init__(self):
        gtk.Toolbar.__init__(self)
        book_search_item = gtk.ToolItem()

        self._search_entry = gtk.Entry()
        self._search_entry.connect('activate', self._search_entry_activate_cb)

        width = int(gtk.gdk.screen_width() / 2)
        self._search_entry.set_size_request(width, -1)

        book_search_item.add(self._search_entry)
        self._search_entry.show()
        self._search_entry.grab_focus()

        self.insert(book_search_item, -1)
        book_search_item.show()

        self._download = ToolButton('go-down')
        self._download.set_tooltip(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self._get_book_cb)
        self.insert(self._download, -1)
        self._download.show()

    def set_activity(self, activity):
        self.activity = activity

    def _search_entry_activate_cb(self, entry):
        self.activity.find_books(entry.props.text)

    def _get_book_cb(self, button):
        self.activity.get_book()
 
    def _enable_button(self,  state):
        self._download.props.sensitive = state

class ReadHTTPRequestHandler(network.ChunkedGlibHTTPRequestHandler):
    """HTTP Request Handler for transferring document while collaborating.

    RequestHandler class that integrates with Glib mainloop. It writes
    the specified file to the client in chunks, returning control to the
    mainloop between chunks.

    """
    def translate_path(self, path):
        """Return the filepath to the shared document."""
        return self.server.filepath

class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            return int(self._info.headers.get('Content-Length'))

    def get_content_type(self):
        """Return the content-type of the download."""
        if self._info is not None:
            return self._info.headers.get('Content-type')
        return None

READ_STREAM_SERVICE = 'read-activity-http'

class GetIABooksActivity(activity.Activity):
    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle)
 
        toolbox = activity.ActivityToolbox(self)
        activity_toolbar = toolbox.get_activity_toolbar()
        activity_toolbar.remove(activity_toolbar.keep)
        activity_toolbar.keep = None
        self.set_toolbox(toolbox)
        
        self._books_toolbar = BooksToolbar()
        toolbox.add_toolbar(_('Books'), self._books_toolbar)
        self._books_toolbar.set_activity(self)
        self._books_toolbar.show()

        toolbox.show()
        self.scrolled = gtk.ScrolledWindow()
        self.scrolled.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.scrolled.props.shadow_type = gtk.SHADOW_NONE
        self.textview = gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_left_margin(50)
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()

        self._download_content_length = 0
        self._download_content_type = None

        self.ls = gtk.ListStore(gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING,  gobject.TYPE_STRING,  \
                                gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING,  \
                                gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING,  gobject.TYPE_STRING)
        tv = gtk.TreeView(self.ls)
        tv.set_rules_hint(True)
        tv.set_search_column(COLUMN_TITLE)
        selection = tv.get_selection()
        selection.set_mode(gtk.SELECTION_SINGLE)
        selection.connect("changed", self.selection_cb)

        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Title'), renderer, text=COLUMN_TITLE_TRUNC)
        col.set_sort_column_id(COLUMN_TITLE)
        tv.append_column(col)
    
        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Volume'), renderer, text=COLUMN_VOLUME)
        col.set_sort_column_id(COLUMN_VOLUME)
        tv.append_column(col)
    
        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Author'), renderer, text=COLUMN_CREATOR_TRUNC)
        col.set_sort_column_id(COLUMN_CREATOR)
        tv.append_column(col)

        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_('Language'), renderer, text=COLUMN_LANGUAGE)
        col.set_sort_column_id(COLUMN_LANGUAGE)
        tv.append_column(col)
    
        self.list_scroller = gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        self.list_scroller.add(tv)
        
        vbox = gtk.VBox()
        vbox.add(self.scrolled)
        vbox.add(self.list_scroller)
        self.set_canvas(vbox)
        tv.show()
        vbox.show()
        self.list_scroller.show()

        # Status of temp file used for write_file:
        self._tempfile = None
        self.toolbox.set_current_toolbar(_TOOLBAR_BOOKS)

    def selection_cb(self, selection):
        tv = selection.get_tree_view()
        model = tv.get_model()
        sel = selection.get_selected()
        if sel:
            model, iter = sel
            self.selected_title = model.get_value(iter,COLUMN_TITLE)
            self.selected_author = model.get_value(iter,COLUMN_CREATOR)
            self.selected_identifier = model.get_value(iter,COLUMN_IDENTIFIER)
            self._books_toolbar._enable_button(True)

    def find_books(self, search_text):
        self._books_toolbar._enable_button(False)
        self.book_selected = False
        self.ls.clear()
        search_tuple = search_text.lower().split()
        if len(search_tuple) == 0:
            self._alert(_('Error'), _('You must enter at least one search word.'))
            self._books_toolbar._search_entry.grab_focus()
            return
        FL = urllib.quote('fl[]')
        SORT = urllib.quote('sort[]')
        search_url = 'http://www.archive.org/advancedsearch.php?q=' +  \
            urllib.quote('(' + search_text.lower() + ') AND format:(DJVU)')
        search_url += '&' + FL + '=creator&' + FL + '=description&' + FL + '=format&' + FL + '=identifier&' + FL + '=language'
        search_url += '&' + FL + '=publicdate&' + FL + '=publisher&' + FL + '=subject&' + FL + '=title&' + FL + '=volume'
        search_url += '&' + SORT + '=title&' + SORT + '&' + SORT + '=&rows=500&save=yes&fmt=csv&xmlsearch=Search'
        gobject.idle_add(self.download_csv,  search_url)
    
    def get_book(self):
        self._books_toolbar._enable_button(False)
        if self.selected_path.startswith('PGA'):
            gobject.idle_add(self.download_book,  self.selected_path.replace('PGA', 'http://gutenberg.net.au'),  self._get_book_result_cb)
        elif self.selected_path.startswith('/etext'):
            gobject.idle_add(self.download_book,  "http://www.gutenberg.org/dirs" + self.selected_path + "108.zip",  self._get_old_book_result_cb)
        else:
            gobject.idle_add(self.download_book,  "http://www.gutenberg.org/dirs" + self.selected_path + "-8.zip",  self._get_iso_book_result_cb)
        
    def download_csv(self,  url):
        print "get csv from",  url
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i.csv' % time.time())
        print 'path=', path
        getter = ReadURLDownloader(url)
        getter.connect("finished", self._get_csv_result_cb)
        getter.connect("progress", self._get_csv_progress_cb)
        getter.connect("error", self._get_csv_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for CSV: ') + url)
           
        self._download_content_type = getter.get_content_type()

    def _get_csv_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)

    def _get_csv_error_cb(self, getter, err):
        _logger.debug("Error getting CSV: %s", err)
        self._alert(_('Error'), _('Error getting CSV') )
        self._download_content_length = 0
        self._download_content_type = None

    def _get_csv_result_cb(self, getter, tempfile, suggested_name):
        print 'Content type:',  self._download_content_type
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_csv_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_csv(tempfile,  suggested_name)

    def process_downloaded_csv(self,  tempfile,  suggested_name):
        reader = csv.reader(open(tempfile,  'rb'))
        reader.next() # skip the first header row.
        for row in reader:
            iter = self.ls.append()
            self.ls.set(iter, 0, row[0],  1,  row[1],  2,  row[2],  3,  row[3],  4,  row[4],  5,  row[5],  \
                        6,  row[6],  7,  row[7],  8,  row[8],  9,  row[9],  \
                        COLUMN_TITLE_TRUNC,  self.truncate(row[COLUMN_TITLE],  75),  \
                        COLUMN_CREATOR_TRUNC,  self.truncate(row[COLUMN_CREATOR],  40))

    def truncate(self,  str,  length):
        if len(str) > length:
            return str[0:length-1] + '...'
        else:
            return str
    
    def _alert(self, title, text=None):
        alert = NotifyAlert(timeout=20)
        alert.props.title = title
        alert.props.msg = text
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)
        self.textview.grab_focus()
