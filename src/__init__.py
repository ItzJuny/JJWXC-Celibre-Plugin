import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from urllib.request import Request, urlopen
import urllib.parse
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Source, Option
from lxml import etree
JJWXC_CONCURRENCY_SIZE=5
PROVIDER_ID = "novel_id"
PROVIDER_NAME = "JJWXC Novels"
JJWXC_NOVEL_URL = 'https://app.jjwxc.net/androidapi/novelbasicinfo?novelId=%s'
class JJWXC_NOVEL_Parser:
    def __init__(self):
        super(JJWXC_NOVEL_Parser, self).__init__()
    def parse_novel(self, novel_url):
        res=urllib.request.urlopen(novel_url)
        res=res.read()
        res_dict = json.loads(res)
        intro = ""
        intro_ = res_dict["novelIntro"].replace("&lt;","").replace("&gt;","").split("br/")
        for i,j in enumerate(intro_):
            if (j != ""):
                intro+=j+"\n"
        novel={}
        novel['url'] = JJWXC_NOVEL_URL%novel_url.split("id=")[-1]
        novel['title'] = "".join(res_dict["novelName"])
        novel['id']="".join(res_dict["novelId"])
        novel['publisher'] = "晋江文学城"
        novel['authors'] = []
        authors="".join(res_dict["authorName"])
        novel['authors'].append(authors)
        novel['cover'] = "".join(res_dict["novelCover"])
        novel['description'] = intro
        novel['tags'] = res_dict["novelTags"].split(",")
        novel['source'] = {
            "id": PROVIDER_ID,
            "description": PROVIDER_NAME,
            "link": "http://www.jjwxc.net/"
        }
        return novel

class NovelLoader:

    def __init__(self):
        self.novel_parser = JJWXC_NOVEL_Parser()

    def load_novel(self, novel_url, log):
        novel = None
        start_time = time.time()
        res = urlopen(Request(novel_url))
        if res.status in [200, 201]:
            log.info("Downloaded:{} Successful,Time {:.0f}ms".format(novel_url, (time.time() - start_time) * 1000))
            novel = self.novel_parser.parse_novel(novel_url)
        return novel

class JjwxcNovelSearcher:

    def __init__(self, max_workers):
        self.novel_loader = NovelLoader()
        self.max_workers = max_workers
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='douban_async')
    def load_novel_urls(self,query,search_pages):
        key_title,key_author=query["title"],query["authors"]
        key_author="".join(key_author)
        encode_keywork = urllib.parse.quote(key_title.encode('gbk'))
        find_dict={}
        for page in range(1,search_pages):
            search_url="http://www.jjwxc.net/search.php?kw=%s&t=1&p=%d&ord=novelscore"%(encode_keywork,page)
            try:
                res = urllib.request.urlopen(search_url)
                res_utf=res.read()
                ress = etree.HTML(res_utf)
                for i in range(2,27):
                    book_name=ress.xpath("//*[@id='search_result']//div[%d]/h3/a//text()"%i)
                    book_url=ress.xpath("//*[@id='search_result']//div[%d]/h3/a/@href"%i)
                    author=ress.xpath("//*[@id='search_result']//div[%d]/div[2]/a/span/text()"%i)
                    book_name = "".join(book_name).replace(" ","").replace("\n","").replace("\r","")
                    book_url = "".join(book_url).replace(" ","").replace("\n","").replace("\r","")
                    author = "".join(author).replace(" ","").replace("\n","").replace("\r","")
                    if key_author in author :
                        find_dict[book_name+"-"+author]=book_url
            except:
                pass
        urls=[]
        for i in find_dict.values():
            urls.append(JJWXC_NOVEL_URL%i.split("id=")[-1])
        return urls
    
    def search_novels(self, query, log):
        novel_urls = self.load_novel_urls(query,10)
        novels = []
        futures = [self.thread_pool.submit(self.novel_loader.load_novel, novel_url, log) for novel_url in novel_urls]
        for future in as_completed(futures):
            novel = future.result()
            if novel is not None:
                novels.append(future.result())
        return novels

class JJWXC_CELIBRE(Source):
    name = 'jjwxc-celibre-plugin'
    description = 'Downloads metadata and covers from jjwxc web site.'
    supported_platforms = ['windows', 'osx', 'linux'] 
    author = "Junono"
    version = (1, 0, 0) 
    minimum_calibre_version = (5, 0, 0)
    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'tags','comments','publisher','identifier:' + PROVIDER_ID
    ])  
    book_searcher = JjwxcNovelSearcher(JJWXC_CONCURRENCY_SIZE)
    options = (
        Option(
            'jjwxc_concurrency_size', 'number', JJWXC_CONCURRENCY_SIZE,
            _('jjwxc concurrency size:'),
            _('The number of jjwxc concurrency cannot be too high!')
        ),
    )
    def get_book_url(self, identifiers): 
        novel_id = identifiers.get(PROVIDER_ID, None)
        if novel_id is not None:
            return (PROVIDER_ID, novel_id, JJWXC_NOVEL_URL % novel_id)

    def download_cover(
            self,
            log,
            result_queue,
            abort,
            title,
            authors,
            identifiers={},
            timeout=30,
            get_best_cover=False):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(
                log,
                rq,
                abort,
                title=title,
                authors=authors,
                identifiers=identifiers
            )
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(
                key=self.identify_results_keygen(
                    title=title, authors=authors, identifiers=identifiers
                )
            )
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)
    def get_cached_cover_url(self, identifiers):
        url = None
        db = identifiers.get(PROVIDER_ID, None)
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from urllib.request import Request, urlopen
import urllib.parse
from calibre import random_user_agent
from calibre.ebooks.metadata.book.base import Metadata
from calibre.ebooks.metadata.sources.base import Source, Option
from lxml import etree
import gzip
from datetime import datetime
import re
JJWXC_CONCURRENCY_SIZE=5
PROVIDER_ID = "novel_id"
PROVIDER_NAME = "JJWXC Novels"
JJWXC_NOVEL_URL = 'http://www.jjwxc.net/onebook.php?novelid=%s'
JJWXC_NOVEL_API = 'https://app.jjwxc.net/androidapi/novelbasicinfo?novelId=%s'
class JJWXC_NOVEL_Parser:
    def __init__(self):
        aa="aa"
    def parse_novel(self, novel_url,log):
        res=urlopen(Request(novel_url,headers={'user-agent': random_user_agent()}))
        res=res.read()
        res_dict = json.loads(res)
        intro = ""
        novel_id="".join(res_dict["novelId"])
        try:
            intro_ = res_dict["novelIntro"].replace("&lt;","").replace("&gt;","").split("br/")
            for i,j in enumerate(intro_):
                if (j != ""):
                    intro+=j+"\n"
        except:
            pass
        try:
            res2 = urlopen(Request(JJWXC_NOVEL_URL%novel_id,headers={'Accept-Encoding': 'gzip, deflate','user-agent':random_user_agent()}))
            res2=res2.read()
            res2=gzip.decompress(res2).decode("gbk").encode("utf-8").decode("utf-8")
            res2=etree.HTML(res2)
            pubdate=res2.xpath("//*[@id='oneboolt']/tbody/tr[4]/td[6]/@title")
            pubdate="".join(pubdate)
            pubdate=pubdate.split("\n")[-1].split(" ")[0].split("：")[-1].rstrip("\n")
        except:
            pubdate=""
        novel={}
        novel['url'] = JJWXC_NOVEL_URL%novel_id
        novel['title'] = "".join(res_dict["novelName"])
        novel['id']=novel_id
        novel['publisher'] = "晋江文学城"
        novel['authors'] = []
        authors="".join(res_dict["authorName"])
        novel['authors'].append(authors)
        novel['cover'] = "".join(res_dict["novelCover"])
        novel['description'] = intro
        novel['tags'] = res_dict["novelTags"].split(",")
        novel['rating']= float(res_dict["novelReviewScore"].split("分")[0]) / 2.0
        novel['publishedDate']= str(pubdate)
        novel['source'] = {
            "id": PROVIDER_ID,
            "description": PROVIDER_NAME,
            "link": "http://www.jjwxc.net/"
        }
        return novel

class NovelLoader:
    def __init__(self):
        self.novel_parser = JJWXC_NOVEL_Parser()

    def load_novel(self, novel_url, log):
        novel = None
        start_time = time.time()
        res = urlopen(Request(novel_url))
        if res.status in [200, 201]:
            log.info("Downloaded:{} Successful,Time {:.0f}ms".format(novel_url, (time.time() - start_time) * 1000))
            novel = self.novel_parser.parse_novel(novel_url,log)
        return novel

class JjwxcNovelSearcher:
    def __init__(self, max_workers):
        self.novel_loader = NovelLoader()
        self.max_workers = max_workers
        self.thread_pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='douban_async')
    def load_novel_urls(self,query,search_pages):
        key_title,key_author=query["title"],query["authors"]
        key_author="".join(key_author)
        encode_keywork = urllib.parse.quote(key_title.encode('gbk'))
        find_dict={}
        for page in range(1,search_pages):
            search_url="http://www.jjwxc.net/search.php?kw=%s&t=1&p=%d&ord=novelscore"%(encode_keywork,page)
            try:
                res = urlopen((Request(search_url,headers={'user-agent': random_user_agent()})))
                res_utf=res.read()
                ress = etree.HTML(res_utf)
                for i in range(2,27):
                    book_name=ress.xpath("//*[@id='search_result']//div[%d]/h3/a//text()"%i)
                    book_url=ress.xpath("//*[@id='search_result']//div[%d]/h3/a/@href"%i)
                    author=ress.xpath("//*[@id='search_result']//div[%d]/div[2]/a/span/text()"%i)
                    book_name = "".join(book_name).replace(" ","").replace("\n","").replace("\r","")
                    book_url = "".join(book_url).replace(" ","").replace("\n","").replace("\r","")
                    author = "".join(author).replace(" ","").replace("\n","").replace("\r","")
                    if len(book_url.split("id=")[-1])!=0:
                        if len(key_author)==0 :
                            find_dict[book_name]=book_url
                        elif key_author in author :
                                find_dict[book_name+"-"+author]=book_url
            except:
                pass
        urls=[]
        for i in find_dict.values():
            urls.append(JJWXC_NOVEL_API%i.split("id=")[-1])
        return urls
    
    def search_novels(self, query, log):
        novel_urls = self.load_novel_urls(query,10)
        novels = []
        futures = [self.thread_pool.submit(self.novel_loader.load_novel, novel_url, log) for novel_url in novel_urls]
        for future in as_completed(futures):
            novel = future.result()
            if novel is not None:
                novels.append(future.result())
        return novels

class JJWXC_CELIBRE(Source):
    name = 'jjwxc-celibre-plugin'
    description = 'Downloads metadata and covers from jjwxc web site.'
    supported_platforms = ['windows', 'osx', 'linux'] 
    author = "Junono"
    version = (1, 0, 0) 
    minimum_calibre_version = (5, 0, 0)
    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset([
        'title', 'authors', 'rating', 'tags','pubdate','comments','publisher','identifier:' + PROVIDER_ID
    ])  
    book_searcher = JjwxcNovelSearcher(JJWXC_CONCURRENCY_SIZE)
    options = (
        Option(
            'jjwxc_concurrency_size', 'number', JJWXC_CONCURRENCY_SIZE,
            _('jjwxc concurrency size:'),
            _('The number of jjwxc concurrency cannot be too high!')
        ),
    )
    def get_book_url(self, identifiers): 
        novel_id = identifiers.get(PROVIDER_ID, None)
        if novel_id is not None:
            return (PROVIDER_ID, novel_id, JJWXC_NOVEL_URL % novel_id)

    def download_cover(
            self,
            log,
            result_queue,
            abort,
            title,
            authors,
            identifiers={},
            timeout=30,
            get_best_cover=False):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(
                log,
                rq,
                abort,
                title=title,
                authors=authors,
                identifiers=identifiers
            )
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(
                key=self.identify_results_keygen(
                    title=title, authors=authors, identifiers=identifiers
                )
            )
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return
        br = self.browser
        log('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            if cdata:
                result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)
    def get_cached_cover_url(self, identifiers):
        url = None
        db = identifiers.get(PROVIDER_ID, None)
        url = self.cached_identifier_to_cover_url(db)
        return url

    def identify(
            self,
            log,
            result_queue,
            abort,
            title,
            authors,
            identifiers={},
            timeout=30):
        concurrency_size = int(self.prefs.get('jjwxc_concurrency_size'))
        if concurrency_size != self.book_searcher.max_workers:
            self.book_searcher = JjwxcNovelSearcher(concurrency_size)
        query={}
        query["title"]=title
        query["authors"]=authors
        novels = self.book_searcher.search_novels(query, log)
        for novel in novels:
            ans = self.to_metadata(novel, log)
            if isinstance(ans, Metadata):
                db = ans.identifiers[PROVIDER_ID]
                if ans.cover:
                    self.cache_identifier_to_cover_url(db, ans.cover)
                self.clean_downloaded_metadata(ans)
                result_queue.put(ans)
            log.info("ans:",novels)
    def to_metadata(self, novel, log):
        if novel:
            mi = Metadata(novel['title'], novel['authors'])
            mi.identifiers = {PROVIDER_ID: novel['id']}
            mi.url = novel['url']
            mi.cover = novel.get('cover', None)
            mi.publisher = novel['publisher']
            mi.comments = novel['description']
            mi.tags = novel.get('tags', [])
            mi.language = 'zh_CN'
            mi.rating = novel['rating']
            pubdate = novel.get('publishedDate', None)
            if pubdate:
                try:
                    if re.compile('^\\d{4}-\\d+$').match(pubdate):
                        mi.pubdate = datetime.strptime(pubdate, '%Y-%m')
                    elif re.compile('^\\d{4}-\\d+-\\d+$').match(pubdate):
                        mi.pubdate = datetime.strptime(pubdate, '%Y-%m-%d')
                except:
                    log.error('Failed to parse pubdate %r' % pubdate)
            log.info('parsed book', mi)
            return mi

if __name__ == "__main__":
    # To run these test use: calibre-debug -e ./__init__.py
    from calibre.ebooks.metadata.sources.test import (
        test_identify_plugin, title_test, authors_test
    )

    test_identify_plugin(
        JJWXC_CELIBRE.name, [
            ({
                 'title': '开端',
                 'authors':'祈祷君',
             }
             , [
                 title_test('开端', exact=True),
                 authors_test(['祈祷君'])
                ]
            )
        ]
    )
