import json
import logging
import os
import re
import time
from datetime import date
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

import ddddocr
import requests


def setup_logger(name, verbose=False):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    if logger.handlers:
        logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger


class FormParser(HTMLParser):
    """提取页面中的表单及其输入项，用于模拟浏览器提交表单。"""

    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None
        self._inputs = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self._current = {
                'action': attrs.get('action', ''),
                'method': attrs.get('method', 'post').lower(),
                'inputs': [],
            }
            self._inputs = self._current['inputs']
        elif self._current is not None and tag == 'input':
            name = attrs.get('name')
            if name is not None:
                self._inputs.append((name, attrs.get('value', '')))
        elif self._current is not None and tag == 'button':
            name = attrs.get('name')
            if name is not None:
                self._inputs.append((name, attrs.get('value', '')))

    def handle_endtag(self, tag):
        if tag == 'form' and self._current is not None:
            self.forms.append(self._current)
            self._current = None
            self._inputs = None


class Gamemale:
    def __init__(self, username, password, questionid='0', answer=None, verbose=False, uids=None):
        self.verbose = verbose
        self.main_logger = setup_logger('GameMale', verbose)
        self.login_logger = setup_logger('登录', verbose)
        self.sign_logger = setup_logger('签到', verbose)
        self.exchange_logger = setup_logger('抽奖', verbose)
        self.space_logger = setup_logger('访问空间', verbose)
        self.say_hi_logger = setup_logger('打招呼', verbose)
        self.stance_logger = setup_logger('表态', verbose)
        self.daily_logger = setup_logger('每日联动', verbose)

        self.login_logger.debug(f"当前用户: {username}")

        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.post_formhash = None
        self.sign_result = None
        self.exchange_result = None
        self.uids = uids if uids is not None else [730713, 62445, 61832]
        self.space_count = 0  # 访问空间
        self.say_hi_count = 0  # 打招呼
        self.stance_count = 0  # 表态数
        self.blog_page = 1  # 日志页码
        self.request_delay = float(os.getenv("GM_DELAY", "0.3"))
        self.username = str(username)
        self.password = str(password)
        self.questionid = questionid
        self.answer = str(answer) if answer else ""
        self.hostname = "www.gamemale.com"
        self.session = requests.session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/91.0.4472.124 Safari/537.36'
            )
        })

    def get_login_formhash(self):
        url = f"https://{self.hostname}/member.php?mod=logging&action=login"
        self.login_logger.debug(f"登录页url: {url}")
        text = self.session.get(url).text
        loginhash_match = re.search(r'<div id="main_messaqge_(.+?)">', text)
        formhash_match = re.search(
            r'<input type="hidden" name="formhash" value="(.+?)" />',
            text
        )
        if not loginhash_match or not formhash_match:
            self.login_logger.debug(f"登录页:\n{text}")
            raise ValueError("无法获取 loginhash 或 formhash")
        loginhash = loginhash_match.group(1)
        formhash = formhash_match.group(1)
        self.login_logger.debug(f"已成功获取登录所需的 loginhash:'{loginhash}'，formhash:'{formhash}'")
        return loginhash, formhash

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(f"看我 slay 验证码 [最多暗娼 {max_retries} 次惹]")

        for attempt in range(1, max_retries + 1):
            update_url = (
                f"https://{self.hostname}/misc.php?mod=seccode&action=update"
                f"&idhash=cSA&0.1234567&modid=member::logging"
            )
            self.login_logger.debug(f"正在从 {update_url} 获取请求验证码的必要参数")
            update_text = self.session.get(update_url).text
            update_match = re.search(r"update=(.+?)&idhash=", update_text)
            if not update_match:
                self.login_logger.debug(f"返回响应:\n{update_text}")
                continue
            update_val = update_match.group(1)
            code_url = (
                f"https://{self.hostname}/misc.php?mod=seccode&update="
                f"{update_val}&idhash=cSA"
            )
            self.login_logger.debug(f"正在从 {code_url} 获取验证码")
            headers = {
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': f"https://{self.hostname}/member.php?mod=logging&action=login",
            }
            code_resp = self.session.get(code_url, headers=headers)
            if not code_resp.content:
                self.login_logger.debug(f"返回响应:\n{code_resp}")
                continue

            code = self.ocr.classification(code_resp.content)

            verify_url = (
                f"https://{self.hostname}/misc.php?mod=seccode&action=check&inajax=1&"
                f"modid=member::logging&idhash=cSA&secverify={code}"
            )
            self.login_logger.debug(f"正在向 {verify_url} 提交识别的验证码")
            res = self.session.get(verify_url).text
            if "succeed" in res:
                self.login_logger.info(f"识别成功: {code} (第{attempt}次)")
                return code
            else:
                self.login_logger.warning(f"错误的识别结果: {code} (第{attempt}次)")
        self.login_logger.error("超出最大重试次数，验证码识别失败")
        return ""

    def login(self) -> bool:
        self.login_logger.info(f"开始登录噜")

        code = self.verify_code()
        if not code:
            self.login_logger.error("缺少验证码，无法执行登录流程")
            return False
        loginhash, formhash = self.get_login_formhash()
        login_url = (
            f"https://{self.hostname}/member.php?mod=logging&action=login"
            f"&loginsubmit=yes&loginhash={loginhash}&inajax=1"
        )
        form_data = {
            'formhash': formhash,
            'referer': f"https://{self.hostname}/",
            'loginfield': self.username,
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer,
            'cookietime': 2592000,
            'seccodehash': 'cSA',
            'seccodemodid': 'member::logging',
            'seccodeverify': code,
        }

        self.login_logger.debug(f"正在向 {login_url} 提交登录表单")
        resp_text = self.session.post(login_url, data=form_data).text
        if "succeed" in resp_text:
            self.login_logger.info("登录成功")

            self.login_logger.debug(f"尝试访问论坛主页，以获取签到所需的 formhash")
            forum_url = f"https://{self.hostname}/forum.php"
            try:
                text = self.session.get(forum_url).text
                formhash_match = re.search(
                    r'<input type="hidden" name="formhash" value="(.+?)" />',
                    text
                )
                if formhash_match:
                    self.post_formhash = formhash_match.group(1)
                    self.login_logger.debug(f"formhash:'{self.post_formhash}'")
                else:
                    self.login_logger.warning("无法获取 formhash")
            except Exception as e:
                self.login_logger.error(f"访问论坛主页出错: {e}")

            return True
        else:
            self.login_logger.error("登录失败")
            self.login_logger.debug(f"原始响应:\n{resp_text}")
            return False

    def sign_gamemale(self):
        self.sign_logger.info("正在签到")
        if not self.post_formhash:
            self.sign_logger.warning("缺少 fromhash ，无法执行签到流程")
            return
        sign_url = (
            f"https://{self.hostname}/k_misign-sign.html?"
            f"operation=qiandao&format=button&formhash={self.post_formhash}"
        )
        try:
            self.sign_logger.debug(f"发送签到请求: {sign_url}")
            resp = self.session.get(sign_url)
            response_text = resp.text
            if response_text.startswith("<?xml"):
                cdata_start = response_text.find("<![CDATA[") + 9
                cdata_end = response_text.find("]]>")
                if cdata_start > 8 and cdata_end > cdata_start:
                    message = response_text[cdata_start:cdata_end]
                else:
                    message = response_text
            else:
                message = response_text
            self.sign_logger.debug(f"签到响应原始内容: {message}")
            if "签到成功" in message:
                sign_status = "签到成功，吸吸"
            elif "已签" in message:
                sign_status = "今日已签，可人"
            else:
                sign_status = "天啦噜，是未知状态"
            self.sign_result = {
                "site": "GameMale",
                "status": sign_status
            }
            self.sign_logger.info(f"结果: {sign_status}")
        except Exception as e:
            self.sign_logger.error(f"签到失败: {e}")
            self.sign_result = {
                "site": "GameMale",
                "status": "天啦噜，请求失败"
            }

    def daily_exchange(self):
        self.exchange_logger.info("正在参与卡片抽奖")
        if not self.post_formhash:
            self.exchange_logger.warning("未能获取 formhash，无法进行日常卡片抽奖")
            return

        timestamp = str(int(time.time() * 1000))
        exchange_url = (
            f"https://{self.hostname}/plugin.php?id=it618_award:ajax&ac=getaward"
            f"&formhash={self.post_formhash}&_={timestamp}"
        )
        headers = {
            'accept': 'application/json, text/javascript, /; q=0.01',
            'referer': f"https://{self.hostname}/it618_award-award.html",
            'x-requested-with': 'XMLHttpRequest',
        }
        try:
            self.exchange_logger.debug(f"发送抽奖请求: {exchange_url}")
            response = self.session.get(exchange_url, headers=headers)
            res_json = response.json()
            self.exchange_logger.debug(f"抽奖响应内容: {res_json}")

            if res_json.get("tipname") == "":
                exchange_status = "没有结果、可能今天已经抽过了"
            elif res_json.get("tipname") == "ok":
                exchange_status = f"成功，吸吸:\n{res_json.get('tipvalue')}"
            else:
                exchange_status = f"你好像进入了一个温暖潮湿的地方:\n{res_json}"

            self.exchange_result = {
                "site": "GameMale",
                "exchange_status": exchange_status
            }
            self.exchange_logger.info(f"结果: {exchange_status}")
        except Exception as e:
            self.exchange_logger.error(f"卡片抽奖失败: {e}")
            self.exchange_result = {
                "site": "GameMale",
                "exchange_status": "天啦噜，抽奖请求失败"
            }

    # ---------- 以下为 JS「一键每日」功能的 Python 移植 ----------

    def _decode_resp(self, resp):
        """确保中文页面/响应能被正确解码。"""
        if not resp.encoding or resp.encoding.lower() in ('iso-8859-1', 'ascii'):
            resp.encoding = resp.apparent_encoding
        return resp.text

    def _tasks_data_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks_data.json')

    def _load_tasks_data(self):
        """对应 JS 的 localStorage['tasksData']：按日期记录每日任务次数。"""
        data = {}
        try:
            with open(self._tasks_data_path(), encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        self._tasks_date = date.today().isoformat()
        if self._tasks_date not in data or not isinstance(data[self._tasks_date], dict):
            data[self._tasks_date] = {}
        self._tasks_data = data

    def _update_tasks_data(self, key):
        """对应 JS 的 updateTasksData(date)。"""
        if not hasattr(self, '_tasks_data'):
            self._load_tasks_data()
        today = self._tasks_data.setdefault(self._tasks_date, {})
        today[key] = today.get(key, 0) + 1
        try:
            with open(self._tasks_data_path(), 'w', encoding='utf-8') as f:
                json.dump(self._tasks_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.daily_logger.warning(f"无法保存每日任务计数: {e}")

    def visit_spaces(self, uids=None):
        """对应 JS space(uid)：访问空间。"""
        self.space_logger.info("正在访问空间")
        uids = uids or self.uids
        for item in uids:
            try:
                url = f"https://{self.hostname}/space-uid-{item}.html"
                resp = self.session.get(url, headers={'Referer': f"https://{self.hostname}/"})
                self.space_count += 1
                self._update_tasks_data('userSpace')
                self.space_logger.info(
                    f"访问空间成功: uid={item} (HTTP {resp.status_code}) "
                    f"({self.space_count}/{len(uids)})"
                )
            except Exception as e:
                self.space_logger.error(f"访问空间失败: uid={item}: {e}")
            time.sleep(self.request_delay)

    def _parse_poke_form(self, html):
        """从打招呼弹窗页面中找出要提交的表单。"""
        parser = FormParser()
        parser.feed(html)
        if not parser.forms:
            return None
        for form in parser.forms:
            names = {name for name, _ in form['inputs']}
            if 'poke' in names or 'ac=poke' in form['action'] or 'formhash' in names:
                return form
        return parser.forms[0]

    def say_hello(self, uids=None):
        """对应 JS sayHello(uid)：打招呼（poke）。"""
        self.say_hi_logger.info("正在打招呼")
        uids = uids or self.uids
        for item in uids:
            try:
                post_url = (
                    f"https://{self.hostname}/home.php?mod=spacecp&ac=poke&op=send"
                    f"&uid={item}&inajax=1"
                )
                headers = {
                    'Referer': f"https://{self.hostname}/forum.php",
                    'Origin': f"https://{self.hostname}",
                }
                # 抓包确认的表单数据（formhash 随会话变化）
                formhash = self.post_formhash or ''
                if not formhash:
                    poke_url = (
                        f"https://{self.hostname}/home.php?mod=spacecp&ac=poke&op=send"
                        f"&uid={item}&handlekey=propokehk_{item}"
                    )
                    page_text = self._decode_resp(
                        self.session.get(poke_url, headers={'Referer': headers['Referer']})
                    )
                    form = self._parse_poke_form(page_text)
                    if form:
                        formhash = dict(form['inputs']).get('formhash', '')
                data = {
                    'referer': f"https://{self.hostname}/forum.php",
                    'pokesubmit': 'true',
                    'formhash': formhash,
                    'from': '',
                    'handlekey': str(item),
                    'iconid': '3',
                    'note': '',
                }
                self.say_hi_logger.debug(f"向 uid={item} 提交打招呼表单: {post_url} data={data}")
                result = self._decode_resp(self.session.post(post_url, data=data, headers=headers))

                if self._is_poke_success(result):
                    self.say_hi_count += 1
                    self._update_tasks_data('sayHello')
                    self.say_hi_logger.info(
                        f"打招呼成功: uid={item} ({self.say_hi_count}/{len(uids)})"
                    )
                else:
                    self.say_hi_logger.warning(f"打招呼未成功: uid={item}，响应: {result[:200]}")
            except Exception as e:
                self.say_hi_logger.error(f"打招呼失败: uid={item}: {e}")
            time.sleep(self.request_delay)

    @staticmethod
    def _is_poke_success(result):
        result = re.sub(r'\s+', '', result)
        if any(mark in result for mark in ('succeed', 'poke_success', '打招呼成功', '已打过招呼', '已发送')):
            return True
        return False

    def _parse_blog_links(self, html, base_url):
        """对应 JS 的 doc.querySelectorAll('dl.bbda dt a')：日志列表链接。"""
        links = []
        dl_pattern = re.compile(
            r'<dl[^>]*class="[^"]*\bbbda\b[^"]*"[^>]*>(.*?)</dl>',
            re.I | re.S,
        )
        a_pattern = re.compile(r'<dt[^>]*>.*?<a[^>]+href="([^"]+)"', re.I | re.S)
        dl_blocks = dl_pattern.findall(html)
        for dl in dl_blocks:
            m = a_pattern.search(dl)
            if m:
                link = urljoin(base_url, unescape(m.group(1)))
                # 过滤掉论坛公告等非日志链接，避免白请求
                if '/blog-' not in link and 'do=blog' not in link:
                    continue
                links.append(link)
        return links

    def _parse_click_url(self, html, base_url):
        """对应 JS 的 doc.querySelector('#click_div a')：日志表态链接。"""
        m = re.search(
            r'<div[^>]+id="click_div"[^>]*>.*?<a[^>]+href="([^"]+)"',
            html,
            re.I | re.S,
        )
        if m:
            url = urljoin(base_url, unescape(m.group(1)))
            return url
        return None

    def stance(self, max_pages=200):
        """对应 JS stance(url)：表态 10 次，翻页直到完成或没有更多日志。"""
        self.stance_logger.info("正在表态")
        page = self.blog_page
        while self.stance_count < 10 and page <= max_pages:
            url = (
                f"https://{self.hostname}/home.php?mod=space&do=blog&view=all"
                f"&catid=14&page={page}"
            )
            try:
                list_text = self._decode_resp(self.session.get(url))
                blog_links = self._parse_blog_links(list_text, url)
                self.stance_logger.debug(f"第 {page} 页找到 {len(blog_links)} 篇日志")
                if not blog_links:
                    self.stance_logger.info("没有更多日志了，停止表态")
                    break
                for blog_url in blog_links:
                    if self.stance_count >= 10:
                        break
                    try:
                        blog_text = self._decode_resp(
                            self.session.get(blog_url, headers={'Referer': url})
                        )
                        click_url = self._parse_click_url(blog_text, blog_url)
                        if not click_url:
                            continue
                        click_resp = self.session.get(
                            click_url,
                            headers={'Referer': f"https://{self.hostname}/forum.php"},
                        )
                        click_text = self._decode_resp(click_resp)
                        compact = re.sub(r'\s+', '', click_text)
                        if '表态成功' in compact or 'click_success' in compact:
                            self.stance_count += 1
                            self._update_tasks_data('stance')
                            self.stance_logger.info(
                                f"表态成功 ({self.stance_count}/10): {blog_url}"
                            )
                        elif '您已表过态' in click_text:
                            self.stance_logger.debug(
                                f"该日志今天已表态过，跳过: {blog_url}"
                            )
                        else:
                            self.stance_logger.debug(
                                f"未表态成功: {blog_url} -> {click_text[:120]}"
                            )
                    except Exception as e:
                        self.stance_logger.warning(f"表态失败 {blog_url}: {e}")
                    time.sleep(self.request_delay)
            except Exception as e:
                self.stance_logger.error(f"获取日志列表第 {page} 页失败: {e}")
                break
            page += 1

        if self.stance_count >= 10:
            self.stance_logger.info("今日表态已完成 (10/10)")
        else:
            self.stance_logger.warning(f"今日表态未满 10 次，当前 {self.stance_count}/10")

    def run(self):
        self.main_logger.info("=== 全自动站街女 ===")
        self._load_tasks_data()
        if not self.login():
            return
        self.sign_gamemale()
        self.daily_exchange()
        self.visit_spaces()
        self.say_hello()
        self.stance()

        self.main_logger.info("=== 今日站街成果 ===")
        if self.sign_result:
            self.main_logger.info(f"签到: {self.sign_result['status']}")
        if self.exchange_result:
            self.main_logger.info(f"抽奖: {self.exchange_result['exchange_status']}")
        self.main_logger.info(f"访问空间: {self.space_count}/{len(self.uids)}")
        self.main_logger.info(f"打招呼: {self.say_hi_count}/{len(self.uids)}")
        self.main_logger.info(f"表态: {self.stance_count}/10")


def main():
    # 本地运行优先使用 GM_USERNAME/GM_PASSWORD；
    # GitHub Actions 工作流里仍可使用 USERNAME/PASSWORD。
    username = os.getenv("GM_USERNAME") or os.getenv("USERNAME")
    password = os.getenv("GM_PASSWORD") or os.getenv("PASSWORD")
    # questionid = os.getenv("QID")
    # answer = os.getenv("ANSWER")

    uids_env = os.getenv("GM_UIDS")
    uids = None
    if uids_env:
        uids = [int(x.strip()) for x in uids_env.split(',') if x.strip()]

    if not username or not password:
        logger = setup_logger("GameMale")
        logger.error("天啦噜，信息不全就想登录？")
        exit(1)
    gm = Gamemale(username, password, verbose=False, uids=uids)
    gm.run()


if __name__ == "__main__":
    main()
