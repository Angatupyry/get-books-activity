#! /usr/bin/env python

# Copyright (C) 2009 James D. Simmons
# Copyright (C) 2009 Sayamindu Dasgupta <sayamindu@laptop.org>
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
import time
import gtk

OLD_TOOLBAR = False
try:
    from sugar.graphics.toolbarbox import ToolbarBox
    from sugar.activity.widgets import StopButton
    from sugar.activity.widgets import ActivityToolbarButton
except ImportError:
    OLD_TOOLBAR = True

from sugar.graphics import style
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.toolcombobox import ToolComboBox
from sugar.graphics.combobox import ComboBox
from sugar.graphics.menuitem import MenuItem
from sugar.graphics import iconentry
from sugar import profile
from sugar.activity import activity
from sugar import network
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from gettext import gettext as _
import dbus
import gobject
import ConfigParser

from listview import ListView
import opds
import languagenames
import devicemanager

_MIMETYPES = {'PDF': u'application/pdf', 'EPUB': u'application/epub+zip'}
_SOURCES = {}
_SOURCES_CONFIG = {}

_logger = logging.getLogger('get-ia-books-activity')


class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            length = self._info.headers.get('Content-Length')
            if length is not None:
                return int(length)
            else:
                return 0

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
        self.max_participants = 1

        self.selected_book = None
        self.queryresults = None
        self._getter = None
        self.show_images = True
        self.languages = {}
        self._lang_code_handler = languagenames.LanguageNames()
        self.catalogs = {}

        if os.path.exists('/etc/get-books.cfg'):
            self._read_configuration('/etc/get-books.cfg')
        else:
            self._read_configuration()

        if OLD_TOOLBAR:

            toolbox = activity.ActivityToolbox(self)
            activity_toolbar = toolbox.get_activity_toolbar()

            self.set_toolbox(toolbox)
            self._books_toolbar = gtk.Toolbar()
            self._add_search_controls(self._books_toolbar)
            self.toolbox.add_toolbar(_('Books'), self._books_toolbar)
            self._books_toolbar.show()
            toolbox.show()
            toolbox.set_current_toolbar(1)

        else:
            toolbar_box = ToolbarBox()
            activity_button = ActivityToolbarButton(self)
            activity_toolbar = activity_button.page
            toolbar_box.toolbar.insert(activity_button, 0)
            self._add_search_controls(toolbar_box.toolbar)

            separator = gtk.SeparatorToolItem()
            separator.props.draw = False
            separator.set_expand(True)
            toolbar_box.toolbar.insert(separator, -1)

            toolbar_box.toolbar.insert(StopButton(self), -1)

            self.set_toolbar_box(toolbar_box)
            toolbar_box.show_all()
            self._books_toolbar = toolbar_box.toolbar

        activity_toolbar.keep.props.visible = False
        self._create_controls()

    def _read_configuration(self, file_name='get-books.cfg'):
        logging.error('Reading configuration from file %s', file_name)
        config = ConfigParser.ConfigParser()
        config.readfp(open(file_name))
        if config.has_option('GetBooks', 'show_images'):
            self.show_images = config.getboolean('GetBooks', 'show_images')
        self.languages = {}
        if config.has_option('GetBooks', 'languages'):
            languages_param = config.get('GetBooks', 'languages')
            for language in languages_param.split(','):
                lang_code = language.strip()
                if len(lang_code) > 0:
                    self.languages[lang_code] = \
                    self._lang_code_handler.get_full_language_name(lang_code)

        for section in config.sections():
            if section != 'GetBooks' and not section.startswith('Catalogs'):
                name = config.get(section, 'name')
                _SOURCES[section] = name
                repo_config = {}
                repo_config['query_uri'] = config.get(section, 'query_uri')
                repo_config['opds_cover'] = config.get(section, 'opds_cover')
                _SOURCES_CONFIG[section] = repo_config

        logging.error('_SOURCES %s', _SOURCES)
        logging.error('_SOURCES_CONFIG %s', _SOURCES_CONFIG)

        for section in config.sections():
            if section.startswith('Catalogs'):
                catalog_source = section.split('_')[1]
                if not catalog_source in _SOURCES_CONFIG:
                    logging.error('There are not a source for the catalog ' +
                                    'section  %s', section)
                    break
                source_config = _SOURCES_CONFIG[catalog_source]
                opds_cover = source_config['opds_cover']
                for catalog in config.options(section):
                    catalog_config = {}
                    catalog_config['query_uri'] = config.get(section, catalog)
                    catalog_config['opds_cover'] = opds_cover
                    catalog_config['source'] = catalog_source
                    self.catalogs[catalog] = catalog_config

        logging.error('languages %s', self.languages)
        logging.error('catalogs %s', self.catalogs)

    def _add_search_controls(self, toolbar):
        book_search_item = gtk.ToolItem()
        toolbar.search_entry = iconentry.IconEntry()
        toolbar.search_entry.set_icon_from_name(iconentry.ICON_ENTRY_PRIMARY,
                                                'system-search')
        toolbar.search_entry.add_clear_button()
        toolbar.search_entry.connect('activate',
                self.__search_entry_activate_cb)
        width = int(gtk.gdk.screen_width() / 5)
        toolbar.search_entry.set_size_request(width, -1)
        book_search_item.add(toolbar.search_entry)
        toolbar.search_entry.show()
        toolbar.insert(book_search_item, -1)
        book_search_item.show()

        toolbar.source_combo = ComboBox()
        toolbar.source_combo.props.sensitive = True
        toolbar.source_changed_cb_id = \
            toolbar.source_combo.connect('changed', self.__source_changed_cb)
        combotool = ToolComboBox(toolbar.source_combo)
        toolbar.insert(combotool, -1)
        combotool.show()

        if len(self.languages) > 0:
            toolbar.language_combo = ComboBox()
            toolbar.language_combo.props.sensitive = True
            combotool = ToolComboBox(toolbar.language_combo)
            toolbar.language_combo.append_item('all', _('Any language'))
            for key in self.languages.keys():
                toolbar.language_combo.append_item(key, self.languages[key])
            toolbar.language_combo.set_active(0)
            toolbar.insert(combotool, -1)
            combotool.show()
            toolbar.language_changed_cb_id = \
                toolbar.language_combo.connect('changed',
                self.__language_changed_cb)

        if len(self.catalogs) > 0:
            bt_catalogs = ToolButton('catalogs')
            bt_catalogs.set_tooltip(_('Catalogs'))

            toolbar.insert(bt_catalogs, -1)
            bt_catalogs.show()
            palette = bt_catalogs.get_palette()

            for key in self.catalogs.keys():
                menu_item = MenuItem(key)
                menu_item.connect('activate',
                    self.__activate_catalog_cb, self.catalogs[key])
                palette.menu.append(menu_item)
                menu_item.show()

        self._device_manager = devicemanager.DeviceManager()
        self._refresh_sources(toolbar)
        self._device_manager.connect('device-added', self.__device_added_cb)
        self._device_manager.connect('device-removed',
                self.__device_removed_cb)

        toolbar.search_entry.grab_focus()
        return toolbar

    def __activate_catalog_cb(self, menu, catalog_config):
        query_language = self.get_query_language()

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.clear()

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        self.queryresults = opds.RemoteQueryResult(catalog_config,
                '', query_language, self.window)
        self.show_message(_('Performing lookup, please wait...'))

        self.queryresults.connect('updated', self.__query_updated_cb)

    def update_format_combo(self, links):
        self.format_combo.handler_block(self.__format_changed_cb_id)
        self.format_combo.remove_all()
        for key in _MIMETYPES.keys():
            if _MIMETYPES[key] in links.keys():
                self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.handler_unblock(self.__format_changed_cb_id)

    def get_search_terms(self):
        return self._books_toolbar.search_entry.props.text

    def __device_added_cb(self, mgr):
        _logger.debug('Device was added')
        self._refresh_sources(self._books_toolbar)

    def __device_removed_cb(self, mgr):
        _logger.debug('Device was removed')
        self._refresh_sources(self._books_toolbar)

    def _refresh_sources(self, toolbar):
        toolbar.source_combo.handler_block(toolbar.source_changed_cb_id)

        #TODO: Do not blindly clear this
        toolbar.source_combo.remove_all()

        for key in _SOURCES.keys():
            toolbar.source_combo.append_item(_SOURCES[key], key)

        devices = self._device_manager.get_devices()

        if len(devices):
            toolbar.source_combo.append_separator()

        for device in devices:
            dev = device[1]
            mount_point = dev.GetProperty('volume.mount_point')
            label = dev.GetProperty('volume.label')
            if label == '' or label is None:
                capacity = int(dev.GetProperty('volume.partition.media_size'))
                label = (_('%.2f GB Volume') % (capacity / (1024.0 ** 3)))
            _logger.debug('Adding device %s' % (label))
            toolbar.source_combo.append_item(mount_point, label)

        toolbar.source_combo.set_active(0)

        toolbar.source_combo.handler_unblock(toolbar.source_changed_cb_id)

    def __format_changed_cb(self, combo):
        self.show_book_data()

    def __language_changed_cb(self, combo):
        search_terms = self.get_search_terms()
        if search_terms == '':
            self.find_books(None)
        else:
            self.find_books(search_terms)

    def __search_entry_activate_cb(self, entry):
        self.find_books(entry.props.text)

    def __get_book_cb(self, button):
        self.get_book()

    def enable_button(self,  state):
        self._download.props.sensitive = state
        self.format_combo.props.sensitive = state

    def _create_controls(self):
        self._download_content_length = 0
        self._download_content_type = None

        self.progressbox = gtk.HBox(spacing=20)
        self.progressbar = gtk.ProgressBar()
        self.progressbar.set_orientation(gtk.PROGRESS_LEFT_TO_RIGHT)
        self.progressbar.set_fraction(0.0)
        self.progressbox.pack_start(self.progressbar, expand=True,
                fill=True)
        self.cancel_btn = gtk.Button(stock=gtk.STOCK_CANCEL)
        self.cancel_btn.connect('clicked', self.__cancel_btn_clicked_cb)
        self.progressbox.pack_start(self.cancel_btn, expand=False,
                fill=False)

        self.msg_label = gtk.Label()

        self.listview = ListView(self._lang_code_handler)
        self.listview.connect('selection-changed', self.selection_cb)

        self.list_scroller = gtk.ScrolledWindow(hadjustment=None,
                vadjustment=None)
        self.list_scroller.set_policy(gtk.POLICY_AUTOMATIC,
                gtk.POLICY_AUTOMATIC)
        vadjustment = self.list_scroller.get_vadjustment()
        vadjustment.connect('value-changed',
                self.__vadjustment_value_changed_cb)
        self.list_scroller.add(self.listview)

        self.scrolled = gtk.ScrolledWindow()
        self.scrolled.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.scrolled.props.shadow_type = gtk.SHADOW_NONE
        self.textview = gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(gtk.WRAP_WORD)
        self.textview.set_justification(gtk.JUSTIFY_LEFT)
        self.textview.set_left_margin(20)
        self.textview.set_right_margin(20)
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()

        vbox_download = gtk.VBox()

        hbox_format = gtk.HBox()
        format_label = gtk.Label(_('Format:'))
        self.format_combo = ComboBox()
        for key in _MIMETYPES.keys():
            self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.props.sensitive = False
        self.__format_changed_cb_id = \
                self.format_combo.connect('changed', self.__format_changed_cb)

        hbox_format.pack_start(format_label, False, False, 10)
        hbox_format.pack_start(self.format_combo, False, False, 10)
        vbox_download.pack_start(hbox_format, False, False, 10)

        self._download = gtk.Button(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self.__get_book_cb)
        vbox_download.pack_start(self._download, False, False, 10)

        bottom_hbox = gtk.HBox()

        if self.show_images:
            self.image = gtk.Image()
            self.add_default_image()
            bottom_hbox.pack_start(self.image, False, False, 10)
        bottom_hbox.pack_start(self.scrolled, True, True, 10)
        bottom_hbox.pack_start(vbox_download, False, False, 10)
        bottom_hbox.show_all()

        vbox = gtk.VBox()
        vbox.pack_start(self.msg_label, False, False, 10)
        vbox.pack_start(self.progressbox, False, False, 10)
        vbox.pack_start(self.list_scroller, True, True, 0)
        vbox.pack_start(bottom_hbox, False, False, 10)
        self.set_canvas(vbox)
        self.listview.show()
        vbox.show()
        self.list_scroller.show()
        self.progressbox.hide()
        self.show_message(
                _('Enter words from the Author or Title to begin search.'))

        self._books_toolbar.search_entry.grab_focus()

    def can_close(self):
        self._lang_code_handler.close()
        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None
        return True

    def selection_cb(self, widget):
        self.clear_downloaded_bytes()
        selected_book = self.listview.get_selected_book()
        if selected_book:
            self.update_format_combo(selected_book.get_download_links())
            self.selected_book = selected_book
            self.show_book_data()

    def show_message(self, text):
        self.msg_label.set_text(text)
        self.msg_label.show()

    def hide_message(self):
        self.msg_label.hide()

    def show_book_data(self):
        self.selected_title = self.selected_book.get_title()
        book_data = _('Title:\t\t') + self.selected_title + '\n\n'
        self.selected_author = self.selected_book.get_author()
        book_data += _('Author:\t\t') + self.selected_author + '\n\n'
        self.selected_publisher = self.selected_book.get_publisher()
        book_data += _('Publisher:\t') + self.selected_publisher + '\n\n'
        book_data += _('Language:\t') + \
                self._lang_code_handler.get_full_language_name(
                    self.selected_book.get_language()) + '\n\n'
        self.download_url = self.selected_book.get_download_links()[\
                self.format_combo.props.value]
        book_data += _('Link:\t\t') + self.download_url
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text('\n' + book_data)
        self.enable_button(True)

        # Cover Image
        self.exist_cover_image = False
        if self.show_images:
            url_image = self.selected_book.get_image_url()
            logging.error('url_image %s', url_image)
            if url_image:
                self.download_image(url_image.values()[0])
            else:
                self.add_default_image()

    def download_image(self,  url):
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        self._getter = ReadURLDownloader(url)
        self._getter.connect("finished", self._get_image_result_cb)
        self._getter.connect("progress", self._get_image_progress_cb)
        self._getter.connect("error", self._get_image_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            self._getter.start(path)
        except:
            _logger.debug("Connection timed out for")
            self.add_default_image()
            self.progressbox.hide()

        self._download_image_content_length = \
                self._getter.get_content_length()
        self._download_image_content_type = self._getter.get_content_type()

    def _get_image_result_cb(self, getter, tempfile, suggested_name):
        self.process_downloaded_cover(tempfile, suggested_name)

    def process_downloaded_cover(self,  tempfile,  suggested_name):
        _logger.debug("Got Cover Image %s (%s)", tempfile, suggested_name)
        self._getter = None
        self.add_image(tempfile)
        self.exist_cover_image = True
        os.remove(tempfile)

    def _get_image_progress_cb(self, getter, bytes_downloaded):
        if self._download_image_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...", bytes_downloaded,
                        self._download_image_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)
        while gtk.events_pending():
            gtk.main_iteration()

    def _get_image_error_cb(self, getter, err):
        self.listview.props.sensitive = True
        self._books_toolbar.enable_button(True)
        self.progressbox.hide()
        _logger.debug("Error getting image: %s", err)
        self.add_default_image()
        self._download_image_content_length = 0
        self._download_image_content_type = None
        self._getter = None

    def add_default_image(self):
        file_path = os.path.join(activity.get_bundle_path(),
                'generic_cover.png')
        self.add_image(file_path)

    def add_image(self, file_path):
        pixbuf = gtk.gdk.pixbuf_new_from_file(file_path)
        self.add_image_buffer(pixbuf)

    def add_image_buffer(self, pixbuf):
        MAX_WIDTH_IMAGE = int(gtk.gdk.screen_width() / 5)
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if width > MAX_WIDTH_IMAGE:
            scale = MAX_WIDTH_IMAGE / float(width)

            pixbuf = pixbuf.scale_simple(int(width * scale),
                    int(height * scale), gtk.gdk.INTERP_BILINEAR)

        self.image.set_from_pixbuf(pixbuf)

    def get_query_language(self):
        query_language = None
        if len(self.languages) > 0:
            query_language = self._books_toolbar.language_combo.props.value
        return query_language

    def find_books(self, search_text=''):
        source = self._books_toolbar.source_combo.props.value

        query_language = self.get_query_language()

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.clear()

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        if search_text is None:
            return
        elif len(search_text) < 3:
            self.show_message(_('You must enter at least 3 letters.'))
            self._books_toolbar.search_entry.grab_focus()
            return

        if source in _SOURCES_CONFIG:
            repo_configuration = _SOURCES_CONFIG[source]
            self.queryresults = opds.RemoteQueryResult(repo_configuration,
                    search_text, query_language, self.window)
        else:
            self.queryresults = opds.LocalVolumeQueryResult( \
                        source, search_text, self.window)

        self.show_message(_('Performing lookup, please wait...'))

        self.queryresults.connect('updated', self.__query_updated_cb)

    def __query_updated_cb(self, query, midway):
        self.listview.populate(self.queryresults)
        if len(self.queryresults) == 0:
            self.show_message(_('Sorry, no books could be found.'))
        elif not midway:
            self.hide_message()
            query_language = self.get_query_language()
            logging.error('LANGUAGE %s', query_language)
            if query_language != 'all' and query_language != 'en':
                # the bookserver send english books if there are not books in
                # the requested language
                only_english = True
                for book in self.queryresults.get_book_list():
                    if book.get_language() == query_language:
                        only_english = False
                        break
                if only_english:
                    self.show_message(
                            _('Sorry, we only found english books.'))

    def __source_changed_cb(self, widget):
        search_terms = self.get_search_terms()
        if search_terms == '':
            self.find_books(None)
        else:
            self.find_books(search_terms)

    def __vadjustment_value_changed_cb(self, vadjustment):

        if not self.queryresults.is_ready():
            return
        try:
            # Use various tricks to update resultset as user scrolls down
            if ((vadjustment.props.upper - vadjustment.props.lower) > 1000 \
                and (vadjustment.props.upper - vadjustment.props.value - \
                vadjustment.props.page_size) / (vadjustment.props.upper - \
                vadjustment.props.lower) < 0.3) or ((vadjustment.props.upper \
                - vadjustment.props.value
                - vadjustment.props.page_size) < 200):
                if self.queryresults.has_next():
                    self.queryresults.update_with_next()
        finally:
            return

    def __cancel_btn_clicked_cb(self, btn):
        if self._getter is not None:
            try:
                self._getter.cancel()
            except:
                _logger.debug('Got an exception while trying' + \
                        'to cancel download')
            self.progressbox.hide()
            self.listview.props.sensitive = True
            self._books_toolbar.search_entry.set_sensitive(True)
            _logger.debug('Download was canceled by the user.')

    def get_book(self):
        self.enable_button(False)
        self.progressbox.show_all()
        gobject.idle_add(self.download_book,  self.download_url)

    def download_book(self,  url):
        self.listview.props.sensitive = False
        self._books_toolbar.search_entry.set_sensitive(False)
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        self._getter = ReadURLDownloader(url)
        self._getter.connect("finished", self._get_book_result_cb)
        self._getter.connect("progress", self._get_book_progress_cb)
        self._getter.connect("error", self._get_book_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            self._getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for ') +
                    self.selected_title)

        self._download_content_length = self._getter.get_content_length()
        self._download_content_type = self._getter.get_content_type()

    def _get_book_result_cb(self, getter, tempfile, suggested_name):
        self.listview.props.sensitive = True
        self._books_toolbar.search_entry.set_sensitive(True)
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_book_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_book(tempfile,  suggested_name)

    def _get_book_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)
        total = self._download_content_length
        self.set_downloaded_bytes(bytes_downloaded,  total)
        while gtk.events_pending():
            gtk.main_iteration()

    def set_downloaded_bytes(self, downloaded_bytes,  total):
        fraction = float(downloaded_bytes) / float(total)
        self.progressbar.set_fraction(fraction)

    def clear_downloaded_bytes(self):
        self.progressbar.set_fraction(0.0)

    def _get_book_error_cb(self, getter, err):
        self.listview.props.sensitive = True
        self.enable_button(True)
        self.progressbox.hide()
        _logger.debug("Error getting document: %s", err)
        self._alert(_('Error: Could not download %s. ' +
                'The path in the catalog seems to be incorrect') %
                self.selected_title)
        self._download_content_length = 0
        self._download_content_type = None
        self._getter = None

    def process_downloaded_book(self,  tempfile,  suggested_name):
        _logger.debug("Got document %s (%s)", tempfile, suggested_name)
        self.create_journal_entry(tempfile)
        self._getter = None

    def create_journal_entry(self,  tempfile):
        journal_entry = datastore.create()
        journal_title = self.selected_title
        if self.selected_author != '':
            journal_title = journal_title + ', by ' + self.selected_author
        journal_entry.metadata['title'] = journal_title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        journal_entry.metadata['mime_type'] = \
                self.format_combo.props.value
        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        textbuffer = self.textview.get_buffer()
        journal_entry.metadata['description'] = \
            textbuffer.get_text(textbuffer.get_start_iter(),
                textbuffer.get_end_iter())
        if self.exist_cover_image:
            journal_entry.metadata['preview'] = self._get_preview_image()

        journal_entry.file_path = tempfile
        datastore.write(journal_entry)
        os.remove(tempfile)
        self.progressbox.hide()
        self._alert(_('Success: %s was added to Journal.') %
            self.selected_title)

    def _get_preview_image(self):
        preview_width, preview_height = style.zoom(300), style.zoom(225)

        pixbuf = self.image.get_pixbuf()
        width, height = pixbuf.get_width(), pixbuf.get_height()

        scale = 1
        if (width > preview_width) or (height > preview_height):
            scale_x = preview_width / float(width)
            scale_y = preview_height / float(height)
            scale = min(scale_x, scale_y)

        pixbuf2 = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, \
                            pixbuf.get_has_alpha(), \
                            pixbuf.get_bits_per_sample(), \
                            preview_width, preview_height)
        pixbuf2.fill(style.COLOR_WHITE.get_int())

        margin_x = int((preview_width - (width * scale)) / 2)
        margin_y = int((preview_height - (height * scale)) / 2)

        pixbuf.scale(pixbuf2, margin_x, margin_y, \
                            preview_width - (margin_x * 2), \
                            preview_height - (margin_y * 2), \
                            margin_x, margin_y, scale, scale, \
                            gtk.gdk.INTERP_BILINEAR)
        preview_data = []

        def save_func(buf, data):
            data.append(buf)

        pixbuf2.save_to_callback(save_func, 'png', user_data=preview_data)
        preview_data = ''.join(preview_data)
        return dbus.ByteArray(preview_data)

    def truncate(self,  word,  length):
        if len(word) > length:
            return word[0:length - 1] + '...'
        else:
            return word

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
