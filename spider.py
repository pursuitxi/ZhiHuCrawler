import csv
import datetime
import argparse
import hashlib
import sys
from urllib.parse import quote
import execjs
import requests
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, async_playwright
import asyncio
import config
from tools import utils
from login import ZhiHuLogin

class ZhiHuSpider:

    def __init__(self, login_type: str, crawler_type: str, timeout=10):
        # self.ua = UserAgent()
        self.headers = {'authority': 'www.zhihu.com',
                        'accept': '*/*',
                        'accept-language': 'zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7,en-GB;q=0.6,en-US;q=0.5',
                        'referer': 'https://www.zhihu.com/',
                        'sec-ch-ua': '"Chromium";v="122", "Not(A:Brand";v="24", "Microsoft Edge";v="122"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"',
                        'sec-fetch-dest': 'empty',
                        'sec-fetch-mode': 'cors',
                        'sec-fetch-site': 'same-origin',
                        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                        'x-requested-with': 'fetch'
                        }
        self.login_type = login_type
        self.crawler_type = crawler_type
        self.timeout = timeout
        self.playwright_page = None
        self.cookies = None

    async def start_crawling(self):
        await self.login()
        if self.crawler_type == 'article':
            await self.get_article_list()
        if self.crawler_type == 'question':
            await self.get_answers_list()
        if self.crawler_type == 'search':
            await self.search()
    async def login(self):
        """ login """
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            zhlogin = ZhiHuLogin(login_type=self.login_type, browser=browser)
            await zhlogin.begin()
            self.cookies = zhlogin.cookies


    """ Article """

    async def get_article_single(self, article_id):
        """ get article """
        utils.logger.info(f"[ZhiHuCrawler.get_article_single] begin crawling article: {article_id}!")
        response = requests.get('https://zhuanlan.zhihu.com/p/' + article_id, cookies=self.cookies, headers=self.headers)
        if 'error' in response:
            utils.logger.error(f"[ZhiHuCrawler.get_article_single] crawler error: {response['error']['message']}")
            sys.exit()
        soup = BeautifulSoup(response.text, 'html.parser')
        article = soup.find('article')
        header = article.find('h1').text
        content = article.find('div', class_="Post-RichTextContainer").text
        data = {'type': 'article',"article_id": article_id, "header": header, "content": content}
        utils.logger.info(f"[ZhiHuCrawler.get_article_single] get article: {data['content']}!")
        return data

    async def get_article_task(self, article_id, semaphore: asyncio.Semaphore):
        async with semaphore:
            try:
                result = await self.get_article_single(article_id)
                return result
            except KeyError as ex:
                utils.logger.error(
                    f"[ZhiHuCrawler.get_article_task] have not fund article detail article_id:{article_id}, err: {ex}")
                return None

    async def get_article_list(self, article_id_list = config.ZHIHU_ARTICLE_ID_LIST):
        """ get article list"""

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [
            self.get_article_task(article_id=article_id, semaphore=semaphore) for article_id in
            article_id_list
        ]
        article_details = await asyncio.gather(*task_list)
        await self.store_csv(article_details,self.crawler_type)


    """  Answers for question  """
    async def get_answers_single(self, question_id: str):
        """ get answers for question """
        utils.logger.info(f"[ZhiHuCrawler.get_answers] begin crawling question: {question_id}!")
        data = {
            'include': 'data[*].is_normal,admin_closed_comment,reward_info,is_collapsed,annotation_action,annotation_detail,collapse_reason,is_sticky,collapsed_by,suggest_edit,comment_count,can_comment,content,editable_content,attachment,voteup_count,reshipment_settings,comment_permission,created_time,updated_time,review_info,relevant_info,question,excerpt,is_labeled,paid_info,paid_info_content,reaction_instruction,relationship.is_authorized,is_author,voting,is_thanked,is_nothelp;data[*].author.follower_count,vip_info,badge[*].topics;data[*].settings.table_of_content.enabled',
            'offset': '',
            'limit': 20,
            'sort_by': 'default',
            'platform': 'desktop'
        }
        response = requests.get(
            'https://www.zhihu.com/api/v4/questions/' + question_id + '/feeds?',
            cookies=self.cookies,
            headers=self.headers,
            params=data
        ).json()
        # print(response.text)
        if 'error' in response:
            utils.logger.error(f"[ZhiHuCrawler.get_answers] crawler error: {response['error']['message']}")
            sys.exit()
        data = response['data']
        next = response['paging']['next']
        is_end = response['paging']['is_end']
        answers = []
        self.clean(data, answers)
        while not is_end:
            try:
                response_next = requests.get(next, cookies=self.cookies, headers=self.cookies).json()
                data = response_next['data']
                next = response_next['paging']['next']
                is_end = response_next['paging']['is_end']
                self.clean(data, answers)

            except Exception as e:
                continue
        return answers

    def clean(self, data: list, answers: list):
        for a in data:
            answer = {}
            author_info = a['target']['author']
            answer['avatar_url'] =author_info.get('avatar_url')
            answer['avatar_url_template'] = author_info.get('avatar_url_template')
            answer['follower_count'] = author_info.get('follower_count')
            answer['gender'] = author_info.get('gender')
            answer['headline'] = author_info.get('headline')
            answer['id'] = author_info.get('id')
            answer['name'] = author_info.get('name')
            answer['user_type'] = author_info.get('user_type')
            answer['url'] = author_info.get('url')
            answer['type'] = a['target_type']
            answer['content'] = BeautifulSoup(a['target']['content'],'html.parser').text
            answer['comment_count'] = a['target'].get('comment_count')
            answer['voteup_count'] = a['target']['voteup_count']
            answer['updated_time'] = a['target']['updated_time']
            utils.logger.info(f"[ZhiHuCrawler.get_answers_single] get answer: {answer['content']}!")
            answers.append(answer)


    async def get_answers_task(self, question_id, semaphore: asyncio.Semaphore):
        async with semaphore:
            try:
                result = await self.get_answers_single(question_id)
                utils.logger.info(f"[ZhiHuCrawler.get_answers_single] question_id : {question_id} crawling finish!")
                await self.store_csv(result,self.crawler_type)
                return result
            except KeyError as ex:
                utils.logger.error(
                    f"[ZhiHuCrawler.get_answers_task] have not fund answers detail question_id:{question_id}, err: {ex}")
                return None

    async def get_answers_list(self, question_id_list = config.ZHIHU_QUESTION_ID_LIST):
        """ get article list"""

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [
            self.get_answers_task(question_id=question_id, semaphore=semaphore) for question_id in
            question_id_list
        ]
        answers_details = await asyncio.gather(*task_list)
        # for answer in answers_details:
        #     await self.store_csv(answer, self.crawler_type)
        utils.logger.info(f"[ZhiHuCrawler.get_answers_single] zhihu crawling task finish!")

    """ Search Keyword """

    async def search(self, keywords = config.KEYWORDS, maxsearch_num = config.CRAWLER_MAX_NOTES_COUNT):
        """ search keywords """
        keywords_list = keywords.split(',')
        for keyword in keywords_list:
            utils.logger.info(f"[ZhiHuCrawler.search] begin crawling {keyword}!")
            params = {
                'gk_version': 'gz-gaokao',
                't': 'general',
                'q': keyword,
                'correction': '1',
                'offset': '0',
                'limit': '20',
                'filter_fields': '',
                'lc_idx': '0',
                'show_all_topics': '0',
                'search_source': 'Normal',
            }

            keyword_url = quote(keyword)
            encode_url = "/api/v4/search_v3?gk_version=gz-gaokao&t=general&q=" + keyword_url + "&correction=1&offset=0&limit=20&filter_fields=&lc_idx=0&show_all_topics=0&search_source=Normal"
            await self.zse_96_signature(encode_url)

            response = requests.get('https://www.zhihu.com/api/v4/search_v3', params=params, cookies=self.cookies,
                                    headers=self.headers).json()
            # print(response)
            if 'error' in response:
                utils.logger.error(f"[ZhiHuCrawler.search] crawler error: {response['error']['message']}")
                sys.exit()

            # next_page = response['paging']['next']
            # is_end = response['paging']['is_end']
            question_id_list = []
            article_id_list = []
            await self.clean_for_search(response,question_ids=question_id_list,article_ids=article_id_list)
            # while not is_end:
            #     response_next = requests.get(next_page).json()
            #     next_page = response['paging']['next']
            #     is_end = response['paging']['is_end']
            #     await self.clean_for_search(response_next, question_ids=question_id_list, article_ids=article_id_list)
            #     if len(question_id_list) + len(article_id_list) >= maxsearch_num:
            #         break
            await self.get_article_list(article_id_list=article_id_list)
            await self.get_answers_list(question_id_list=question_id_list)

    async def zse_96_signature(self, url):
        d_c0 = await self.cookies['d_c0']
        f = "+".join(["101_3_3.0", url, d_c0])
        fmd5 = hashlib.new('md5', f.encode()).hexdigest()
        with open('g_encrypt.js', 'r', encoding='utf-8') as f:
            ctx1 = execjs.compile(f.read())
        result = ctx1.call('b', fmd5)
        encrypt_str = "2.0_%s" % result
        self.headers['x-zse-93'] = '101_3_3.0'
        self.headers['x-zse-96'] = encrypt_str

    async def clean_for_search(self, response: dict, question_ids: list, article_ids: list):

        if response.get('data'):
            for item in response.get('data'):
                object = item.get('object')
                type = object.get('type')
                if type == 'answer':
                    question = object.get("question")
                    question_id = question.get("id")
                    question_ids.append(question_id)
                    utils.logger.info(f"[ZhiHuCrawler.search] crawling question: {question_id}")
                elif type == 'article':
                    article_id = object.get("id")
                    article_ids.append(article_id)
                    utils.logger.info(f"[ZhiHuCrawler.search] crawling article: {article_id}")
        return question_ids, article_ids


    async def store_csv(self, data, crawler_type: str):

        file_name = 'data/' + crawler_type + '_' + str(datetime.date.today()) + '.csv'

        with open(file_name, 'a', encoding='utf8', newline='') as f:
            w = csv.writer(f)
            w.writerow(data[0].keys())
            for x in data:
                w.writerow(x.values())

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ZhiHu crawler program.')
    parser.add_argument('--lt', type=str, help='Login type (qrcode | cookie)',
                        choices=["qrcode", "cookie"], default=config.LOGIN_TYPE)
    parser.add_argument('--type', type=str, help='crawler type (search | article | question)',
                        choices=["search", "article", "question"], default=config.CRAWLER_TYPE)

    args = parser.parse_args()

    spider = ZhiHuSpider(login_type=args.lt, crawler_type=args.type)
    asyncio.run(spider.start_crawling())
