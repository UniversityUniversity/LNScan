#!/usr/bin/env python
# -*- encoding: utf-8 -*-
# my[at]lijiejie.com

import urlparse
import httplib
import logging
import re
import threading
import Queue
from bs4 import BeautifulSoup
import time
import glob
from interface import InfoDisScannerBase


class InfoDisScanner(InfoDisScannerBase):
    def __init__(self, url, lock, timeout=600, depth=2):
        self.START_TIME = time.time()
        self.TIME_OUT = timeout
        self.LINKS_LIMIT = 20       # max number of links
        self.final_severity = 0
        self.schema, self.host, self.path = self._parse_url(url)
        self.max_depth = self._cal_depth(self.path)[1] + depth     # max depth to scan
        self.url_queue = Queue.Queue()    # all urls in queue to scan
        self.urls_in_queue = []           # urls already in queue
        self._init_rules()
        _path, _depth = self._cal_depth(self.path)
        self._enqueue(_path)
        self.crawl_index(_path)
        self.lock = threading.Lock()
        self.results = {}

    @staticmethod
    def _parse_url(url):
        _ = urlparse.urlparse(url, 'http')
        if not _.netloc:
            _ = urlparse.urlparse('http://' + url, 'http')
        assert(_.netloc != '')
        return _.scheme, _.netloc, _.path if _.path else '/'

    @staticmethod
    def _cal_depth(url):
        """
        calculate the depth of a given URL, return tuple (url, depth)
        """
        if url.find('#') >= 0: url = url[:url.find('#')]    # cut off fragment
        if url.find('?') >= 0: url = url[:url.find('?')]    # cut off query
        if url.startswith('//'): return '', 10000    # //www.baidu.com/index.php, ignored
        if url.startswith('javascript:'): return '', 10000    # no http protocol, ignored

        if url.startswith('http'):
            _ = urlparse.urlparse(url, 'http')
            if _.netloc == self.host:    # same hostname
                url = _.path
            else:
                return '', 10000         # not the same hostname, ignored

        url = url.replace('//', '/')
        if not url: return '/', 1         # http://www.example.com

        if url[0] != '/': url = '/' + url

        url = url[: url.rfind('/')+1]
        depth = len(url.split('/')) - 1
        return url, depth

    def _init_rules(self):
        try:
            self.url_dict = []
            p_severity = re.compile('{severity=(\d)}')
            p_tag = re.compile('{tag="([^"]+)"}')
            p_status = re.compile('{status=(\d{3})}')
            p_content_type = re.compile('{type="([^"]+)"}')
            p_content_type_no = re.compile('{type_no="([^"]+)"}')
            for rule_file in glob.glob('rules/*.txt'):
                infile = open(rule_file, 'r')
                for url in infile:
                    url = url.strip().replace('{hostname}', self.host)
                    if url.startswith('/'):
                        _ = p_severity.search(url)
                        severity = int(_.group(1)) if _ else 3
                        _ = p_tag.search(url)
                        tag = _.group(1) if _ else ''
                        _ = p_status.search(url)
                        status = int(_.group(1)) if _ else 0
                        _ = p_content_type.search(url)
                        content_type = _.group(1) if _ else ''
                        _ = p_content_type_no.search(url)
                        content_type_no = _.group(1) if _ else ''
                        url = url.split()[0]
                        self.url_dict.append((url, severity, tag, status, content_type, content_type_no))
                        # print (url, severity, tag, status, content_type, content_type_no)
                infile.close()
        except Exception, e:
            logging.error('[Exception in InfoDisScanner._load_dict] %s' % e)

    def _http_request(self, url, timeout=4):
        try:
            if not url: url = '/'
            _host = self.host.split(":")
            conn_fuc = httplib.HTTPSConnection if self.schema == 'https' else httplib.HTTPConnection
            conn = conn_fuc(host=_host[0], port=_host[1], timeout=timeout)
            conn.request(method='GET', url=url,
                         headers={'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/38.0.2125.111 Safari/537.36 BBScan/1.0'}
            )
            resp = conn.getresponse()
            resp_headers = dict(resp.getheaders())
            status = resp.status
            if resp_headers.get('content-type', '').find('text') >= 0 or resp_headers.get('content-type', '').find('html') >= 0 or \
                            int(resp_headers.get('content-length', '0')) <= 1048576:
                html_doc = self._decode_response_text(resp.read())
            else:
                html_doc = ''
            conn.close()
            return status, resp_headers, html_doc
        except Exception, e:
            #logging.error('[Exception in InfoDisScanner._http_request] %s' % e)
            return -1, {}, ''

    @staticmethod
    def _decode_response_text(rtxt, charset=None):
        if charset:
            try:
                return rtxt.decode(charset)
            except:
                pass
        encodings = ['UTF-8', 'GB2312', 'GBK', 'iso-8859-1', 'big5']
        for _ in encodings:
            try:
                return rtxt.decode(_)
            except:
                pass
        try:
            return rtxt.decode('ascii', 'ignore')
        except:
            pass
        raise Exception('Fail to decode response Text')

    def get_status(self, url):
        return self._http_request(url)[0]

    def _enqueue(self, url):
        if url in self.urls_in_queue:
            return False
        elif len(self.urls_in_queue) >= self.LINKS_LIMIT:
            return False
        else:
            self.urls_in_queue.append(url)

        for _ in self.url_dict:
            full_url = url.rstrip('/') + _[0]
            url_description = {'prefix': url.rstrip('/'), 'full_url': full_url}
            item = (url_description, _[1], _[2], _[3], _[4], _[5])
            self.url_queue.put(item)
        return True

    def crawl_index(self, path):
        try:
            status, headers, html_doc = self._http_request(path)
            if status != 200:
                return
            soup = BeautifulSoup(html_doc, "html.parser")
            links = soup.find_all('a')
            for l in links:
                url = l.get('href', '')
                url, depth = self._cal_depth(url)
                if depth <= self.max_depth:
                    self._enqueue(url)
        except Exception, e:
            logging.error('Exception in crawl_index: %s' % e)

    def _get_url(self):
        """
        get url with global lock
        """
        self.lock.acquire()
        if self.url_index_offset < self.len_urls:
            url = self.urls[self.url_index_offset]
        else:
            url = None, None, None
        self.url_index_offset += 1
        self.lock.release()
        return url

    def _update_severity(self, severity):
        if severity > self.final_severity:
            self.final_severity = severity

    def _scan_worker(self):
        while self.url_queue.qsize() > 0:
            if time.time() - self.START_TIME > self.TIME_OUT:
                print '[ERROR] Timed out task %s' % self.host
                return
            try:
                item = self.url_queue.get(timeout=1.0)
            except:
                return
            try:
                url_description, severity, tag, code, content_type, content_type_no = item
                url = url_description['full_url']
                prefix = url_description['prefix']
            except Exception, e:
                logging.error('[InfoDisScanner._scan_worker][1] Exception: %s' % e)
                continue
            if not item or not url:
                break

            try:
                status, headers, html_doc = self._http_request(url)
                if status in [200, 301, 302, 303]:
                    if code and status != code:
                        continue
                    if not tag or html_doc.find(tag) >= 0:
                        if content_type and headers.get('content-type', '').find(content_type) < 0 or \
                            content_type_no and headers.get('content-type', '').find(content_type_no) >=0:
                            continue
                        self.lock.acquire()
                        # print '[+] [Prefix:%s] [%s] %s' % (prefix, status, 'http://' + self.host +  url)
                        if not prefix in self.results:
                            self.results[prefix]= []
                        self.results[prefix].append({'status':status, 'url': '%s://%s%s' % (self.schema, self.host, url)} )
                        self._update_severity(severity)
                        self.lock.release()

                if len(self.results) >= 30:
                    print 'More than 30 vulnerabilities found for [%s], could be false positive.' % self.host
                    return
            except Exception, e:
                logging.error('[InfoDisScanner._scan_worker][2][%s] Exception %s' % (url, e))

    def scan(self, threads=20):
        # if self._status == -1:
        #     return False
        threads_list = []
        for i in range(threads):
            t = threading.Thread(target=self._scan_worker)
            threads_list.append(t)
            t.start()
        for t in threads_list:
            t.join()
        for key in self.results.keys():
            if len(self.results[key]) > 10:
                del self.results[key]
        return self.host, self.results, self.final_severity


def batch_scan(url, q_results, lock, threads_num, timeout):
        print 'Scan', url
        a = InfoDisScanner(url, lock, timeout*60)
        host, results, severity = a.scan(threads=threads_num)
        if results:
            q_results.put((host, results, severity))

        if results:
            for key in results.keys():
                for url in results[key]:
                    print  '[+] [%s] %s' % (url['status'], url['url'])


