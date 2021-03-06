# -*- coding: utf-8 -*-

from __future__ import print_function

import logging
import math
import os
import subprocess
import sys
import time

from six import iteritems


class Downloader(object):
    """
    Base downloader class.

    Every subclass should implement the _start_download method.

    Usage::

      >>> import downloaders
      >>> d = downloaders.SubclassFromDownloader()
      >>> d.download('http://example.com', 'save/to/this/file')

    Inspired by https://github.com/coursera-dl/coursera
    """

    def _start_download(self, url, filename):
        """
        Actual method to download the given url to the given file.
        This method should be implemented by the subclass.
        """
        raise NotImplementedError("Subclasses should implement this")

    def download(self, url, filename):
        """
        Download the given url to the given file. When the download
        is aborted by the user, the partially downloaded file is also removed.
        """

        try:
            self._start_download(url, filename)
        except KeyboardInterrupt as e:
            logging.info(
                'Keyboard Interrupt -- Removing partial file: %s', filename)
            try:
                os.remove(filename)
            except OSError:
                pass
            raise e


class ExternalDownloader(Downloader):
    """
    Downloads files with an extrnal downloader.

    We could possibly use python to stream files to disk,
    but this is slow compared to these external downloaders.

    :param session: Requests session.
    :param bin: External downloader binary.
    """

    # External downloader binary
    bin = None

    def __init__(self, session, bin=None):
        self.session = session
        self.bin = bin or self.__class__.bin

        if not self.bin:
            raise RuntimeError("No bin specified")

    def _create_command(self, url, filename):
        """
        Create command to execute in a subprocess.
        """
        raise NotImplementedError("Subclasses should implement this")

    def _start_download(self, url, filename):
        command = self._create_command(url, filename)
        logging.debug('Executing %s: %s', self.bin, command)
        try:
            subprocess.call(command)
        except OSError as e:
            msg = "{0}. Are you sure that '{1}' is the right bin?".format(
                e, self.bin)
            raise OSError(msg)


class WgetDownloader(ExternalDownloader):
    """
    Uses wget, which is robust and gives nice visual feedback.
    """

    bin = 'wget'

    def _create_command(self, url, filename):
        return [self.bin, url, '-O', filename, '--no-cookies',
                '--no-check-certificate']


class CurlDownloader(ExternalDownloader):
    """
    Uses curl, which is robust and gives nice visual feedback.
    """

    bin = 'curl'

    def _create_command(self, url, filename):
        return [self.bin, url, '-k', '-#', '-L', '-o', filename]


def format_bytes(bytes):
    """
    Get human readable version of given bytes.
    Ripped from https://github.com/rg3/youtube-dl
    """
    if bytes is None:
        return 'N/A'
    if type(bytes) is str:
        bytes = float(bytes)
    if bytes == 0.0:
        exponent = 0
    else:
        exponent = int(math.log(bytes, 1024.0))
    suffix = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'][exponent]
    converted = float(bytes) / float(1024 ** exponent)
    return '{0:.2f}{1}'.format(converted, suffix)


class DownloadProgress(object):
    """
    Report download progress.
    Inspired by https://github.com/rg3/youtube-dl
    """

    def __init__(self, total):
        if total in [0, '0', None]:
            self._total = None
        else:
            self._total = int(total)

        self._current = 0
        self._start = 0
        self._now = 0

        self._finished = False

    def start(self):
        self._now = time.time()
        self._start = self._now

    def stop(self):
        self._now = time.time()
        self._finished = True
        self._total = self._current
        self.report_progress()

    def read(self, bytes):
        self._now = time.time()
        self._current += bytes
        self.report_progress()

    def calc_percent(self):
        if self._total is None:
            return '--%'
        percentage = int(float(self._current) / float(self._total) * 100.0)
        done = int(percentage / 2)
        return '[{0: <50}] {1}%'.format(done * '#', percentage)

    def calc_speed(self):
        dif = self._now - self._start
        if self._current == 0 or dif < 0.001:  # One millisecond
            return '---b/s'
        return '{0}/s'.format(format_bytes(float(self._current) / dif))

    def report_progress(self):
        """Report download progress."""
        percent = self.calc_percent()
        total = format_bytes(self._total)

        speed = self.calc_speed()
        total_speed_report = '{0} at {1}'.format(total, speed)

        report = '\r{0: <56} {1: >30}'.format(percent, total_speed_report)

        if self._finished:
            print(report)
        else:
            print(report, end="")
        sys.stdout.flush()


class NativeDownloader(Downloader):
    """
    'Native' python downloader -- slower than the external downloaders.

    :param session: Requests session.
    """

    def __init__(self, session):
        self.session = session

    def _start_download(self, url, filename):
        logging.info('Downloading %s -> %s', url, filename)

        attempts_count = 0
        error_msg = ''
        while attempts_count < 5:
            r = self.session.get(url, stream=True)

            if r.status_code is not 200:
                logging.warning(
                    'Probably the file is missing from the AWS repository...'
                    ' waiting.')

                if r.reason:
                    error_msg = r.reason + ' ' + str(r.status_code)
                else:
                    error_msg = 'HTTP Error ' + str(r.status_code)

                wait_interval = 2 ** (attempts_count + 1)
                msg = 'Error downloading, will retry in {0} seconds ...'
                print(msg.format(wait_interval))
                time.sleep(wait_interval)
                attempts_count += 1
                continue

            content_length = r.headers.get('content-length')
            progress = DownloadProgress(content_length)
            chunk_sz = 1048576
            with open(filename, 'wb') as f:
                progress.start()
                while True:
                    data = r.raw.read(chunk_sz)
                    if not data:
                        progress.stop()
                        break
                    progress.read(len(data))
                    f.write(data)
            r.close()
            return True

        if attempts_count == 5:
            logging.warning('Skipping, can\'t download file ...')
            logging.error(error_msg)
            return False


def get_downloader(session, args):
    """
    Decides which downloader to use.
    """
    external = {
        'wget': WgetDownloader,
        'curl': CurlDownloader
    }

    for bin, class_ in iteritems(external):
        if getattr(args, bin):
            return class_(session, bin=getattr(args, bin))

    return NativeDownloader(session)
