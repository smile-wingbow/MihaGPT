# -*- coding: utf-8 -*-
import time

from metagpt.roles import Role
from metagpt.config2 import Config
from metagpt.schema import Message
from metagpt.roles.role import RoleReactMode
from metagpt.actions.add_requirement import UserRequirement
from metagpt.actions import Action
from metagpt.utils.common import OutputParser
from metagpt.utils.parse_html import WebPage

from homeassistant.homeassistant_storage import HaStorage

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

from pathlib import Path
from typing import Any, Callable, ClassVar
from requests import post, get
from datetime import datetime
import urllib.parse
import pytz
import ruamel.yaml
import uuid
from ruamel.yaml.compat import StringIO
import json
import re
import tiktoken

import logging

logger = logging.getLogger("xiaogpt")
from pydantic import TypeAdapter
from bs4 import BeautifulSoup
import random

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions

# hass相关的全局变量
areas = []
area_id_list = []
devices = []
entity_type = []
entities = []

read_entity_limit = 30

AUTOMATION_YAML_PATH = '/data/homeassistant/automations.yaml'


# AUTOMATION_YAML_PATH = './automations.yaml'

def scrape_website(driver, url):
    try:
        driver.get(url)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        inner_text = driver.execute_script("return document.body.innerText;")
        html = driver.page_source
    except Exception as e:
        inner_text = f"Fail to load page content for {e}"
        html = ""
    return WebPage(inner_text=inner_text, html=html, url=url)


def truncated_string(
        string: str,
        model: str,
        max_tokens: int,
        print_warning: bool = True,
) -> str:
    """Truncate a string to a maximum number of tokens."""
    encoding = tiktoken.encoding_for_model(model)
    encoded_string = encoding.encode(string)
    truncated_string = encoding.decode(encoded_string[:max_tokens])
    if print_warning and len(encoded_string) > max_tokens:
        print(f"Warning: Truncated string from {len(encoded_string)} tokens to {max_tokens} tokens.")
    return truncated_string


def update_entity_type(entity_type):
    # 确保 "sensor" 和 "binary_sensor" 中的任意一个出现时，另一个也在列表中
    if "sensor" in entity_type and "binary_sensor" not in entity_type:
        entity_type.append("binary_sensor")
    elif "binary_sensor" in entity_type and "sensor" not in entity_type:
        entity_type.append("sensor")

    # 确保 "switch" 和 "light" 中的任意一个出现时，另一个也在列表中
    if "switch" in entity_type and "light" not in entity_type:
        entity_type.append("light")
    elif "light" in entity_type and "switch" not in entity_type:
        entity_type.append("switch")

    return entity_type


def convert_utc_to_local(json_data, local_tz='Asia/Shanghai'):
    # 正则表达式匹配 UTC 时间格式
    utc_time_pattern = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00')
    local_timezone = pytz.timezone(local_tz)

    def convert_time(match):
        utc_time_str = match.group(0)
        # 将时间字符串转换为 datetime 对象
        utc_dt = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
        # 将 UTC 时间转换为本地时区时间
        local_dt = utc_dt.astimezone(local_timezone)
        return local_dt.isoformat()

    # 转换 JSON 数据中的所有 UTC 时间字符串
    json_str = json.dumps(json_data, ensure_ascii=False)
    converted_json_str = utc_time_pattern.sub(convert_time, json_str)
    return json.loads(converted_json_str)


def parse_jason_code(rsp):
    pattern = r"```json(.*)```"
    match = re.search(pattern, rsp, re.DOTALL)
    code_text = match.group(1) if match else rsp
    return code_text


def parse_yaml_code(rsp):
    pattern = r"```yaml(.*?)```"
    matches = re.findall(pattern, rsp, re.DOTALL)
    return matches


def is_json(json_str):
    try:
        json_object = json.loads(json_str)
    except ValueError as e:
        return False
    return True


def is_yaml(yaml_str):
    yaml = ruamel.yaml.YAML()
    try:
        yaml_object = yaml.load(yaml_str)
    except ValueError as e:
        return False
    return True


# 通过ha的rest api获取实体的状态
def get_entity_state_in_cache(entity_id):
    ha_storage = HaStorage()
    entities = ha_storage.get_entity_by_id(entity_id)
    if entities and isinstance(entities, list):
        return entities[0]
    else:
        return None


# 通过ha的rest api获取实体的状态
def get_entities_history_rest_api(entity_id_list, ha_address, ha_port, token, timestamp, end_time):
    def translate(response_data):
        history = json.loads(response_data)

        result = []

        for entity_list in history:
            entity_id = entity_list[0].get("entity_id")
            if entity_id:
                entity_history = {}
                history_key = f"history of {entity_id}"
                entity_history[history_key] = entity_list
                result.append(entity_history)
        return result

    base_url = "http://" + ha_address + ":" + ha_port + "/api/history/period"
    if timestamp:
        base_url += "/" + timestamp

    entities = ""
    for entity_id in entity_id_list:
        entities += entity_id + ","

    entities = entities[:-1]

    params = {
        "end_time": end_time,
        "filter_entity_id": entities
    }

    encoded_url = f"{base_url}?{urllib.parse.urlencode(params)}"

    encoded_url += "&minimal_response&no_attributes&significant_changes_only"

    # if end_time:
    #     url += "&end_time=" + end_time
    headers = {
        "Authorization": "Bearer " + token
    }

    # logger.info(f"get_entities_history_rest_api: {encoded_url}")

    response = get(encoded_url, headers=headers)

    if response.status_code == 200:
        # print(response.text)
        response_local = convert_utc_to_local(response.text)

        # 解析 JSON 字符串为 Python 对象
        entity = translate(response_local)

        return entity
    else:
        logger.error(f"Error: Received response with status code {response.status_code}")
        return None


# 通过ha的rest api获取实体的状态
def get_entity_state_rest_api(entity_id, ha_address, ha_port, token):
    url = "http://" + ha_address + ":" + ha_port + "/api/states/" + entity_id
    headers = {
        "Authorization": "Bearer " + token
    }

    response = get(url, headers=headers)

    if response.status_code == 200:
        # print(response.text)
        response_local = convert_utc_to_local(response.text)

        # 解析 JSON 字符串为 Python 对象
        entity = json.loads(response_local)

        return entity
    else:
        logger.error(f"Error: Received response with status code {response.status_code}")
        return None


# 通过ha的rest api获取所有实体的状态
def get_entity_states_rest_api(ha_address, ha_port, token):
    url = "http://" + ha_address + ":" + ha_port + "/api/states"
    headers = {
        "Authorization": "Bearer " + token
    }

    response = get(url, headers=headers)

    if response.status_code == 200:
        # print(response.text)
        response_local = convert_utc_to_local(response.text)

        # 解析 JSON 字符串为 Python 对象
        entity = json.loads(response_local)

        return entity
    else:
        logger.error(f"Error: Received response with status code {response.status_code}")
        return None


# 通过ha的rest api调用服务
def call_ha_service_rest_api(entity_id, service, option_name, option, ha_address, ha_port, token):
    domain = entity_id.split(".")[0]

    url = "http://" + ha_address + ":" + ha_port + "/api/services/" + domain + "/" + service
    headers = {
        "Authorization": "Bearer " + token
    }

    json_data = {}
    if option_name and option_name != "NA" and option_name != "hvac_mode/NA" and option and option != "NA" and option != "cool/NA":
        json_data = {
            "entity_id": entity_id,
            option_name: option
        }
    else:
        json_data = {
            "entity_id": entity_id
        }

    logger.info(f"ha url:{url}, ha headers:{headers}, ha json:{json_data}")

    response = post(url, headers=headers, json=json_data)

    if response.status_code == 200:
        # print(response.text)
        response_local = convert_utc_to_local(response.text)

        # 解析 JSON 字符串为 Python 对象
        result = json.loads(response_local)

        return result
    else:
        logger.error(f"Error: Received response with status code {response.status_code}")
        return None


# 通过ha的rest api重新载入automation的yaml配置文件
def reload_automation_rest_api(ha_address, ha_port, token):
    url = "http://" + ha_address + ":" + ha_port + "/api/services/automation/reload"
    headers = {
        "Authorization": "Bearer " + token
    }

    response = post(url, headers=headers)

    if response.status_code == 200:
        return True
    else:
        logger.error(f"Error: Received response with status code {response.status_code}")
        return False


# 通过ha的rest api获取错误日志
def get_ha_errlog_rest_api(ha_address, ha_port, token):
    url = "http://" + ha_address + ":" + ha_port + "/api/error_log"
    headers = {
        "Authorization": "Bearer " + token
    }

    response = get(url, headers=headers)

    if response.status_code == 200:
        # 尝试将响应内容从ISO-8859-1编码转换为UTF-8编码
        log_content = response.content.decode('utf-8')
        return log_content
    else:
        logger.error(f"Error: Received response with status code {response.status_code}")
        return None


class PlayMedia(Action):
    name: str = "PlayMedia"

    run: ClassVar[callable]

    def __init__(self, **data: Any):
        super().__init__(**data)

    async def run(self, context):
        logger.info(f"play media:{context}")

        rsp_result = context

        return rsp_result


class SearchWeb(Action):
    name: str = "SearchWeb"

    run: ClassVar[callable]

    SEARCH_TOPIC_PROMPT: str = """从用户的对话历史中提取用户输入的原文，不要加上其他任何的词或字。"""

    SEARCH_KEYWORD_PROMPT: str = """Please provide up to 2 necessary keywords related to your research topic for Google search. \
    Your response must be in JSON format, 并用中文输出, for example: ["keyword1", "keyword2"]."""

    WEB_BROWSE_AND_ANSWER_PROMPT: str = """
    ### Constraints:
    - 现在的时间是：{time}

    ### Context:
    {query}

    ### Workflow
    think step by step，分析context，从Reference Information中查找答案，并按照OutputFormat里的格式输出：
    1. If the question can be directly answered using the text,  please reply with "answer_got" set to "True", and provide a comprehensive summary of the text in Chinese.
    2. If the question can not be directly answered using the text, or if the text is entirely unrelated to the question, please reply "answer_got" set to "False" and a summary "没有找到相关答案".
    3. Include all relevant factual information, numbers, statistics, etc., if available.

    ### Reference Information
    {content}

    ### OutputFormat:
    分析结果以json结构输出，json格式如下：       
    ```json 
   {{
        "answer_got": "True/False",
        "summary": "内容总结"
    }}
   ```  
    """

    def __init__(self, tts_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback

    async def run(self, context, web_driver):
        # 等待目标XHR请求的出现
        def wait_for_xhr_request(driver, url_part, timeout=60):
            """
            等待特定的XHR请求出现
            """
            start_time = time.time()
            while time.time() - start_time < timeout:
                for request in driver.requests:
                    if request.response and url_part in request.url:
                        print(f"捕获到目标XHR请求: {request.url}")
                        return True
                time.sleep(0.3)
            return False

        answered = False

        # 搜索百度
        keywords = await self._aask(self.SEARCH_KEYWORD_PROMPT, [context])
        try:
            keywords = OutputParser.extract_struct(keywords, list)
            keywords = TypeAdapter(list[str]).validate_python(keywords)
        except Exception as e:
            logger.exception(f"fail to get keywords related to the research topic '{context}' for {e}")
            keywords = [context]

        result_title = []
        result_link = []

        keywords_str = " ".join(keywords)
        logger.info(f"搜索关键词：{keywords_str}")

        # 发起get请求
        web_driver.get('http://www.baidu.com/')
        time.sleep(random.uniform(1, 2))

        # 等待搜索框出现
        WebDriverWait(web_driver, 10).until(
            expected_conditions.presence_of_element_located((By.NAME, 'wd')))

        input_element = web_driver.find_element(By.NAME, 'wd')
        input_element.send_keys(keywords_str)
        input_element.submit()

        # 最多等待10秒直到浏览器标题栏中出现我希望的字样（比如查询关键字出现在浏览器的title中）
        WebDriverWait(web_driver, 10).until(
            expected_conditions.title_contains(keywords_str))

        # 使用BeautifulSoup解析页面内容
        bsobj = BeautifulSoup(web_driver.page_source, 'html.parser')

        # 查找搜索结果
        elements = bsobj.find_all('div', {'class': re.compile('c-container')})
        for element in elements:
            title = element.h3.a.text.strip() if element.h3 and element.h3.a else ""
            link = element.h3.a['href'] if element.h3 and element.h3.a else ""
            result_title.append(title)
            result_link.append(link)
            print('Title:', title)
            print('Link:', link)
            print('=' * 70)

        # 查找详细搜索结果
        elements = bsobj.find_all('span', {'class': re.compile('content-right')})
        for element in elements:
            title = element.text
            result_title.append(title)
            print('Title:', title)
            print('=' * 70)

        if result_title:
            now = datetime.now()
            prompt = self.WEB_BROWSE_AND_ANSWER_PROMPT.format(query=context,
                                                              content=json.dumps(result_title, ensure_ascii=False),
                                                              time=now)

            logger.info(f"总结联网搜索结果提示词：{prompt}")

            rsp = await self._aask(prompt)

            logger.info(rsp)

            result = json.loads(parse_jason_code(rsp))

            # 如果搜索有结果播报结果，如果没有结果则搜索子链接
            if "answer_got" in result and result["answer_got"] == "True":
                answered = True
                await self.tts_callback(result["summary"])
                return result["summary"]
            # 以下为详细链接的搜索，耗时太长，先注释不使用
            # elif result_link and isinstance(result_link, list):
            #     for link in result_link:
            #         if link:
            #             logger.info(f"开始搜索以下链接：{link}")
            #             contents = scrape_website(web_driver, link)
            #             logger.info(f"搜索结果：{contents}")
            #             if not result_link:
            #                 contents = [contents]
            #             now = datetime.now()
            #             prompt_template = self.WEB_BROWSE_AND_ANSWER_PROMPT.format(query=context, content="{}", time=now)
            #             if contents and isinstance(contents, list) and contents[0]:
            #                 content = contents[0].inner_text
            #                 token = count_output_tokens(prompt_template + context, self.llm.model) + 100
            #                 # 仅截取8192个字节的搜索结果
            #                 if token < 8192:
            #                     now = datetime.now()
            #                     prompt = self.WEB_BROWSE_AND_ANSWER_PROMPT.format(query=context, content=content, time=now)
            #                     rsp_content = await self._aask(prompt)
            #
            #                     result = json.loads(parse_jason_code(rsp_content))
            #
            #                     if "answer_got" in result and result["answer_got"] == "True":
            #                         await self.tts_callback(result["summary"])
            #                         return result["summary"]
            #                 else:
            #                     truncated_content = truncated_string(content, self.llm.model, 8192, print_warning=False)
            #                     now = datetime.now()
            #                     prompt = self.WEB_BROWSE_AND_ANSWER_PROMPT.format(query=context, content=truncated_content,
            #                                                                       time=now)
            #                     rsp_content = await self._aask(prompt)
            #                     if "answer_got" in result and result["answer_got"] == "True":
            #                         await self.tts_callback(result["summary"])
            #                         return result["summary"]

        if not answered:
            # 搜索360 普通搜索
            keywords = await self._aask(self.SEARCH_KEYWORD_PROMPT, [context])
            try:
                keywords = OutputParser.extract_struct(keywords, list)
                keywords = TypeAdapter(list[str]).validate_python(keywords)
            except Exception as e:
                logger.exception(f"fail to get keywords related to the research topic '{context}' for {e}")
                keywords = [context]

            keywords_str = " ".join(keywords)
            logger.info(f"搜索关键词：{keywords_str}")

            # 发起get请求
            web_driver.get('https://www.so.com/')
            time.sleep(random.uniform(1, 2))

            # 等待搜索框出现
            WebDriverWait(web_driver, 10).until(
                expected_conditions.presence_of_element_located((By.NAME, 'q')))

            input_element = web_driver.find_element(By.NAME, 'q')
            input_element.send_keys(keywords_str)

            # 模拟按下回车键来提交搜索
            input_element.send_keys(Keys.RETURN)

            # 最多等待10秒直到浏览器标题栏中出现我希望的字样（比如查询关键字出现在浏览器的title中）
            WebDriverWait(web_driver, 10).until(
                expected_conditions.title_contains(keywords_str))

            # 使用BeautifulSoup解析页面内容
            bsobj = BeautifulSoup(web_driver.page_source, 'html.parser')

            result_title = []
            result_link = []

            # 查找搜索结果
            elements = bsobj.find_all('li', {'class': re.compile('res-list')})
            for element in elements:
                title = element.h3.a.text.strip() if element.h3 and element.h3.a else ""
                link = element.h3.a['href'] if element.h3 and element.h3.a else ""
                result_title.append(title)
                result_link.append(link)
                print('Title:', title)
                print('Link:', link)
                print('=' * 70)

            # 查找详细搜索结果
            elements = bsobj.find_all('div', {'class': re.compile('res-rich')})
            for element in elements:
                title = element.text
                result_title.append(title)
                print('Title:', title)
                print('=' * 70)

            if result_title:
                now = datetime.now()
                prompt = self.WEB_BROWSE_AND_ANSWER_PROMPT.format(query=context, content=json.dumps(result_title,
                                                                                                    ensure_ascii=False),
                                                                  time=now)

                logger.info(f"总结联网搜索结果提示词：{prompt}")

                rsp = await self._aask(prompt)

                logger.info(rsp)

                result = json.loads(parse_jason_code(rsp))

                # 如果搜索有结果播报结果，如果没有结果则搜索子链接
                if "answer_got" in result and result["answer_got"] == "True":
                    await self.tts_callback(result["summary"])
                    return result["summary"]

        if not answered:
            question = await self._aask(self.SEARCH_TOPIC_PROMPT, [context])

            # 访问360 AI搜索
            web_driver.get('https://so.360.com/')
            time.sleep(random.uniform(1, 2))

            # 等待搜索框出现
            WebDriverWait(web_driver, 10).until(
                expected_conditions.presence_of_element_located((By.ID, 'composition-input')))

            # 获取搜索框
            input_element = web_driver.find_element(By.ID, 'composition-input')
            input_element.send_keys(question)

            # 模拟按下回车键来提交搜索
            input_element.send_keys(Keys.RETURN)

            # 最多等待10秒直到页面出现搜索结果框
            WebDriverWait(web_driver, 10).until(
                expected_conditions.presence_of_element_located((By.CLASS_NAME, 'markdown-container')))

            # 等待60，如果出现指定的XHR请求，则AI搜索结果加载完毕
            if wait_for_xhr_request(web_driver, "https://so.360.com/api/chat/ask_further/v2", timeout=60):
                # 使用BeautifulSoup解析页面内容
                bsobj = BeautifulSoup(web_driver.page_source, 'html.parser')

                # 查找搜索结果数量
                container_elements = bsobj.find_all('div', {'class': 'markdown-container'})
                if container_elements:
                    result_title = []
                    for element in container_elements:
                        result = element.text
                        if result:
                            result_title.append(result)
                    if result_title:
                        now = datetime.now()
                        prompt = self.WEB_BROWSE_AND_ANSWER_PROMPT.format(query=context,
                                                                          content=json.dumps(result_title,
                                                                                             ensure_ascii=False),
                                                                          time=now)

                        logger.info(f"总结联网搜索结果提示词：{prompt}")

                        rsp = await self._aask(prompt)

                        logger.info(rsp)

                        result = json.loads(parse_jason_code(rsp))

                        # 如果搜索有结果播报结果，如果没有结果则搜索子链接
                        if "answer_got" in result and result["answer_got"] == "True":
                            answered = True
                            await self.tts_callback(result["summary"])
                            return result["summary"]

        return "没有找到相关答案"


class Gossip(Action):
    name: str = "Gossip"

    run: ClassVar[callable]

    GOSSIP_PROMPT_TEMPLATE: str = """    
    ## OutputFormat:
    回答结果以json结构输出，json格式如下：
       ```json 
       {{"answer":""}}
       ```   

    ## Time:
    现在的时间是：{time}，

    ## context
    <<<
    {context}
    >>>，    
    上述对话中涉及的角色包括：
        1.Human，用户。                    
    """

    def __init__(self, tts_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback

    async def run(self, context):
        now = datetime.now()

        gossip_prompt = self.GOSSIP_PROMPT_TEMPLATE.format(context=context, time=now)

        rsp_gossip = await self._aask(gossip_prompt)

        logger.info(rsp_gossip)

        rsp_result = parse_jason_code(rsp_gossip)

        if is_json(rsp_result):
            rsp_json = json.loads(rsp_result)
            await self.tts_callback(rsp_json["answer"])
            # self.tts_callback(rsp_json["answer"])
            logger.info(f"rsp_result:{rsp_result}")

            return rsp_result
        else:
            return ""


class Call_ha_service_response(Action):
    name: str = "Call_ha_service_response"

    run: ClassVar[callable]

    CALL_HA_SERVICE_RESPONSE_TEMPLATE: str = """
    ## context
    <<<
    {context}
    >>>，
    你是一个home assistant专家，你通过home assistant的API调用home assistant中设备实体的服务，

    待调用的home assistant设备实体id和服务列表如下：
    ---
    {entities_services}
    ---，

    调用的结果如下：$$${results}$$$，

    以json结构输出你对调用服务结果的说明，在"result"中详细说明home assistant服务调用的结果，json格式如下：
    ```json 
    {{"call entity service":[{{"entity_id":"climate.xiaomi_mc5_1642_air_conditioner","service":"set_hvac_mode","option_name":"hvac_mode/NA","option":"cool/NA"}}...],"result":"content"}}
    ```
    """

    async def run(self, context: str, entities_services, ha_address, ha_port, token):
        """
        entities_services格式：[{{"entity_id":"climate.xiaomi_mc5_1642_air_conditioner","service":"set_hvac_mode","option_name":"hvac_mode/NA","option":"cool/NA"}}...]
        """
        results = []
        if entities_services and isinstance(entities_services, list):
            for entity_service in entities_services:
                if "entities" in entity_service and entity_service["entities"] and isinstance(
                        entity_service["entities"], list):
                    entities = entity_service["entities"]
                    for entity in entity_service["entities"]:
                        entity_id = entity["entity_id"]
                        service = entity["service"]
                        option_name = ""
                        option = ""
                        if "option" in entity and entity["option"]:
                            mode = service.replace("set_", "")
                            if mode:
                                option_name = mode
                                option = entity["option"]

                        result = call_ha_service_rest_api(entity_id, service, option_name, option, ha_address, ha_port,
                                                          token)
                        # result = "home assistant空调已成功切换到制冷模式。当前温度为34.8°C，设定温度为26.0°C。"
                        results.append(result)

        return json.dumps(results, ensure_ascii=False)

class AskUserToConfirm(Action):
    name: str = "AskUserToConfirm"

    run: ClassVar[callable]

    def __init__(self, tts_callback, listen_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback
        self.listen_callback = listen_callback

    async def run(self, content: str):
        await self.tts_callback(content)
        return ""


class AskNewRequirement(Action):
    name: str = "AskNewRequirement"

    run: ClassVar[callable]

    def __init__(self, tts_callback, listen_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback
        self.listen_callback = listen_callback

    async def run(self, content: str):
        logger.info(f"AskNewRequirement content:{content}")
        await self.tts_callback(content)
        rsp = ""
        return rsp


class Evaluate(Action):
    name: str = "Evaluate"

    run: ClassVar[callable]

    EVALUATE_PROMPT_TEMPLATE: str = """
    ## context
    <<<
    {context}
    >>>，    
    上述对话中涉及的角色包括：
        1.Human，用户。
        2.Doorman，用户对话分类器，分析用户对话
        2.Interpreter，根据对话的分析结果，选择下一步的动作。
        3.Actuator，根据调度器的结果，执行对应的动作，包括读取设备实体数据历史、调用home assistant设备实体服务、向用户确认、读取设备实体状态、播放提示语音等。

    ## Role:
    home assistant资深专家

    ## Profile:
    - Language:中文
    - Description: 你是一个home assistant专家，通过与用户的对话给出建议，帮助他们访问和控制home assistant设备。

    ## Constraints:
    - 现在的时间是：{time}

    ## Skill:  

    ## Workflow:
    think step by step，分析context，根据分析结果选择以下选项之一，并按照选项中的要求根据OutputFormat输出相关内容：
    分支1. 如果下一步需要先读取home assistant中的设备或设备的状态，则输出"next_step"为2，并输出其他内容，结束。
    分支2. 如果下一步需要先控制home  assistant中的设备，则输出"next_step"为3，并输出其他内容，结束。
    分支3. 如果下一步需要先新增或修改home assistant的自动化场景，则输出"next_step"为4。
    分支4. 如果你觉得自动化场景生成存在问题，请选择此项，输出"next_step"为4，并输出"result"。
    分支5. 如果你认为已经完全满足了用户的需求/意图或潜在意图，请选择此项，输出"next_step"为5，并输出"result"来告诉用户结果，并询问用户有什么新的需求。
    分支6. 如果你认为已经完全满足了用户的需求/意图或潜在意图，且用户答复没有其他新的需求，请选择此项。输出"next_step"为6，并输出"result"来告诉用户结果。
    分支9. 如果你觉得多次交互后还是无法回答用户的问题，请选择此项，输出"next_step"为9，并输出"result"对用户表示歉意。
    分支10.如果下一步需要先初始化home assistant的自动化场景，则输出"next_step"为10。
    Please note that "next_step" only needs a number, no need to add any other text.


    ## OutputFormat:
    分析结果以json结构输出，json格式如下：
       ```json 
       {{
            "next_step": 4,
            "question": "需要确认的内容",
            "result": "结果说明"
        }}
       ```   
    """

    async def run(self, context):
        now = datetime.now()
        evaluate_prompt = self.EVALUATE_PROMPT_TEMPLATE.format(context=context, time=now)
        logger.info(f"结果评估器提示词：{evaluate_prompt}")
        rsp_evaluate = await self._aask(evaluate_prompt)
        logger.info(rsp_evaluate)
        rsp_result = parse_jason_code(rsp_evaluate)

        return rsp_result


class Judger(Role):
    name: str = "Judger"
    profile: str = "Judger"

    _act: ClassVar[callable]
    get_memories: ClassVar[callable]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))

        self._watch(
            [Read_ha_state_response, Call_ha_service_response, Read_ha_history_response, Gen_ha_automation, SearchWeb])
        self.set_actions([Evaluate(config=gpt4o_llm)])
        self._set_react_mode(react_mode=RoleReactMode.BY_ORDER.value)

    def get_memories(self, k=0):
        context = self.rc.memory.get(k=k)

        context_str = ""
        if isinstance(context, list):
            for msg in context:
                if isinstance(msg, Message):
                    # if "Classify_L1" in msg.cause_by:
                    #     continue
                    context_str += "《《" + msg.__str__() + "》》,"
                else:
                    context_str += "《《" + json.dumps(msg, ensure_ascii=False) + "》》,"
        else:
            context_str = json.dumps(context, ensure_ascii=False)

        return context_str

    async def _act(self) -> Message:
        todo = self.rc.todo

        context = self.get_memories()

        code_text = await todo.run(context)
        msg = Message(content=code_text, role=self.name, cause_by=type(todo))

        return msg


class Read_ha_history_response(Action):
    name: str = "Read_ha_history_response"

    run: ClassVar[callable]

    async def run(self, context: str, entity_id_list, ha_address, ha_port, token, start_time, end_time):
        entities_history = []
        if entity_id_list:
            states = get_entities_history_rest_api(entity_id_list, ha_address, ha_port, token, start_time, end_time)
            entities_history.extend(states)

        return json.dumps(entities_history, ensure_ascii=False)


class Gen_ha_automation(Action):
    name: str = "Gen_ha_automation"

    find_log_segment: ClassVar[callable]
    run: ClassVar[callable]

    def find_log_segment(self, log_text, search_str, current_time, time_delta=10):
        # 将日志文本按行拆分
        log_lines = log_text.splitlines()

        # 初始化结果变量
        result = ""
        found = False
        log_time = None

        for line in log_lines:
            if line.strip():  # 如果行非空
                # 提取时间戳
                try:
                    split_line = line.split()
                    if len(split_line) >= 2:  # 确保有足够的元素来提取时间戳
                        timestamp_str = split_line[0] + " " + split_line[1]
                        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                    else:
                        print("Line does not contain a valid timestamp.")
                except ValueError:
                    continue

                # 检查是否包含搜索字符串
                if search_str in line:
                    if not log_time or log_time < timestamp:
                        log_time = timestamp
                        found = True
                        result = line  # 开始记录
                elif found:
                    result += "\n" + line

        # 判断时间差是否在允许范围内
        if found and log_time and abs((current_time - log_time).total_seconds()) <= time_delta:
            return result

    async def run(self, context: str, automation_str, ha_address, ha_port, token):
        yaml = ruamel.yaml.YAML()
        yaml.preserve_quotes = True  # 尝试保留原始引号
        yaml.indent(mapping=2, sequence=4, offset=2)

        # 打开并写入 automations.yaml 文件
        file_path = AUTOMATION_YAML_PATH
        with open(file_path, 'r', encoding='utf-8') as file:
            existing_data = yaml.load(file) or []

        automations = json.loads(automation_str)
        yaml_list = automations["yaml_list"]

        alias_list = []

        if yaml_list and isinstance(yaml_list, list):
            for yaml_str in yaml_list:
                # 生成新的 UUID 并创建新的结构
                new_id = uuid.uuid4()
                data = yaml.load(yaml_str)
                alias_list.append(data["alias"])
                # 创建新的结构，将原始内容放到 'id' 下
                new_data = {
                    'id': str(new_id),
                    'initial_state': False,
                    **data
                }

                # 将新的结构添加到现有数据列表的末尾
                existing_data.append(new_data)

                # 使用 StringIO 模拟文件写入
                stream = StringIO()
                yaml.dump(existing_data, stream)

                with open(file_path, 'w', encoding='utf-8') as file:
                    file.write(stream.getvalue())

        reload_result = reload_automation_rest_api(ha_address, ha_port, token)
        error_list = []

        if reload_result:
            errlog = get_ha_errlog_rest_api(ha_address, ha_port, token)
            for alias in alias_list:
                now = datetime.now()
                # error = self.find_log_segment(errlog, '打开一楼办公室灯', now, time_delta=10)
                error = self.find_log_segment(errlog, alias, now, time_delta=10)
                if error:
                    error_list.append(error)

                if error:
                    # 如果有错误，则删除当前写入的内容
                    with open(file_path, 'r', encoding='utf-8') as file:
                        existing_data = yaml.load(file) or []
                    existing_data = [item for item in existing_data if item.get('id') != str(new_id)]
                    # 将修改后的数据写回 YAML 文件
                    stream = StringIO()
                    yaml.dump(existing_data, stream)

                    with open(file_path, 'w', encoding='utf-8') as file:
                        file.write(stream.getvalue())

            if error_list:
                return error_list
            else:
                result = "自动化场景生成成功，请在home assistant配置-自动化页面启用。"
                return result
        else:
            result = "call /api/services/automation/reload error"
            return result


class Actuator(Role):
    name: str = "Actuator"
    profile: str = "Actuator"

    _act: ClassVar[callable]
    get_memories: ClassVar[callable]
    _think: ClassVar[callable]

    def __init__(self, tts_callback, listen_callback, ha_address: str, ha_port: str,
                 ha_token: str, web_driver, **kwargs):
        super().__init__(**kwargs)

        self.tts_callback = tts_callback
        self.listen_callback = listen_callback
        self.ha_address = ha_address
        self.ha_port = ha_port
        self.ha_token = ha_token
        self.web_driver = web_driver

        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))

        self._watch([Classify_L2_R, Classify_L2_W, Classify_L2_Auto, Automation_initialize])
        self.set_actions([Read_ha_state_response(config=gpt4o_mini_llm),
                          Call_ha_service_response(config=gpt4o_mini_llm),
                          AskUserToConfirm(self.tts_callback, self.listen_callback),
                          Read_ha_history_response(config=gpt4o_mini_llm), Gen_ha_automation,
                          AskNewRequirement(self.tts_callback, self.listen_callback),
                          SearchWeb(self.tts_callback, config=gpt4o_mini_llm),
                          Gossip(self.tts_callback, config=gpt4o_mini_llm)])
        self._set_react_mode(react_mode=RoleReactMode.BY_ORDER.value)

    def get_memories(self, k=0):
        context = self.rc.memory.get(k=k)

        context_str = ""
        if isinstance(context, list):
            for msg in context:
                if isinstance(msg, Message):
                    # if "Classify_L1" in msg.cause_by:
                    #     continue
                    context_str += "《《" + msg.__str__() + "》》,"
                else:
                    context_str += "《《" + json.dumps(msg, ensure_ascii=False) + "》》,"
        else:
            context_str = json.dumps(context, ensure_ascii=False)

        return context_str

    async def _act(self) -> Message:
        news = self.rc.news[0]
        logger.info(f"Actuator news:{news}")

        todo = self.rc.todo
        context = self.get_memories()  # use all memories as context

        if news:
            if isinstance(news, Message) and news.role != "user":
                if is_json(news.content):
                    msg = json.loads(news.content)
                    if isinstance(msg, dict):
                        if "next_step" in msg and msg["next_step"] == 1:
                            # self.rc.todo = Read_ha_history_response()
                            code_text = await todo.run(context, msg["read_entity_list"], self.ha_address,
                                                       self.ha_port, self.ha_token,
                                                       msg["start_time"], msg["end_time"])
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and (
                                msg["next_step"] == 2 or msg["next_step"] == 4):
                            # self.rc.todo = AskUserToConfirm(self.tts_callback)

                            try:
                                msg_content = news.content
                                if msg_content:
                                    logger.info(f"msg content: {msg_content}")
                                    # 如果doorman_content是字符串，则解析它
                                    if isinstance(msg_content, str):
                                        try:
                                            content_data = json.loads(msg_content)
                                            logger.info(f"Parsed content json data: {content_data}")

                                            content = ""
                                            if "result" in content_data and content_data["result"] and content_data[
                                                "result"] != "NA" and content_data["result"] != "result/NA":
                                                content = content_data["result"]
                                            if "question" in content_data and content_data["question"] and content_data[
                                                "question"] != "NA" and content_data["question"] != "question/NA":
                                                content = content + "，" + content_data["question"]

                                            logger.info(f"AskUserToConfirm content:{content}")
                                            code_text = await todo.run(content)

                                            msg = Message(content=json.dumps(code_text, ensure_ascii=False),
                                                          role="Human", cause_by=type(todo))

                                            return msg
                                        except json.JSONDecodeError as e:
                                            logger.info(f"Error decoding msg_content JSON: {e}")
                                    else:
                                        logger.info("msg content is not a JSON string, it's a dictionary:",
                                                    msg_content)
                                else:
                                    logger.info("Doorman key not found in message data.")
                            except json.JSONDecodeError as e:
                                logger.info(f"Error decoding message content JSON: {e}")
                        elif "next_step" in msg and msg["next_step"] == 3:
                            # self.rc.todo = Call_ha_service_response()
                            code_text = await todo.run(context, msg["write_entity_list"], self.ha_address,
                                                       self.ha_port, self.ha_token)
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 7:
                            # self.rc.todo = Read_ha_state_response()
                            code_text = await todo.run(context, msg["read_entity_list"], self.ha_address,
                                                       self.ha_port,
                                                       self.ha_token)
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and (msg["next_step"] == 6 or msg["next_step"] == 8):
                            try:
                                msg_content = news.content

                                if msg_content:
                                    logger.info(f"msg content: {msg_content}")
                                    if isinstance(msg_content, str):
                                        try:
                                            content_data = json.loads(msg_content)

                                            result = content_data["result"]
                                            await self.tts_callback(result)
                                            # self.tts_callback(result)
                                            logger.info(f"result:{result}")
                                        except json.JSONDecodeError as e:
                                            logger.info(f"Error decoding msg_content JSON: {e}")
                                    else:
                                        logger.info("msg content is not a JSON string, it's a dictionary:",
                                                    msg_content)
                                else:
                                    logger.info("Doorman key not found in message data.")
                            except json.JSONDecodeError as e:
                                logger.info(f"Error decoding message content JSON: {e}")
                        elif "next_step" in msg and msg["next_step"] == 5:
                            # self.rc.todo = AskNewRequirement(self.tts_callback)
                            try:
                                msg_content = news.content
                                if msg_content:
                                    if isinstance(msg_content, str):
                                        try:
                                            content_data = json.loads(msg_content)

                                            content = ""
                                            if "result" in content_data and content_data["result"] and content_data[
                                                "result"] != "NA" and content_data["result"] != "result/NA":
                                                content = content_data["result"]
                                            if "question" in content_data and content_data["question"] and content_data[
                                                "question"] != "NA" and content_data["question"] != "question/NA":
                                                content = content + "，" + content_data["question"]

                                            logger.info(f"AskNewRequirement content:{content}")
                                            code_text = await todo.run(content)

                                            # 提出新需求后清空历史对话记录
                                            for key, value in self.rc.env.roles.items():
                                                value.rc.memory.clear()
                                                value.rc.msg_buffer.pop_all()

                                            msg = Message(content=code_text, role="Human", cause_by=type(todo))
                                            return msg
                                        except json.JSONDecodeError as e:
                                            logger.info(f"Error decoding msg_content JSON: {e}")
                                    else:
                                        logger.info("msg content is not a JSON string, it's a dictionary:",
                                                    msg_content)
                                else:
                                    logger.info("Doorman key not found in message data.")
                            except json.JSONDecodeError as e:
                                logger.info(f"Error decoding message content JSON: {e}")
                        elif "next_step" in msg and msg["next_step"] == 0:
                            # self.rc.todo = SearchWeb(self.tts_callback)
                            if "tips" in msg and msg["tips"] and msg["tips"] != "NA" and msg["tips"] != "tips/NA":
                                await self.tts_callback(msg["tips"])

                            code_text = await todo.run(news, self.web_driver)

                            # # 已满足用户需求，提出新需求后清空历史对话记录
                            # for key, value in self.rc.env.roles.items():
                            #     value.rc.memory.clear()
                            #     value.rc.msg_buffer.pop_all()

                            # msg = Message(content=code_text, role="Human", cause_by=type(todo))
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == -1:
                            # self.rc.todo = Gossip(self.tts_callback)
                            code_text = await todo.run(news)

                            # 已满足用户需求，提出新需求后清空历史对话记录
                            for key, value in self.rc.env.roles.items():
                                value.rc.memory.clear()
                                value.rc.msg_buffer.pop_all()

                            # msg = Message(content=code_text, role="Human", cause_by=type(todo))
                            msg = Message(content="", role="", cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 100:
                            # self.rc.todo = Gen_ha_automation()
                            code_text = await todo.run(context, news.content, self.ha_address,
                                                       self.ha_port, self.ha_token)
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg

        # self.rc.todo = Gossip(self.tts_callback)
        code_text = await todo.run(news)

        # 清空历史对话记录
        for key, value in self.rc.env.roles.items():
            value.rc.memory.clear()
            value.rc.msg_buffer.pop_all()

        # msg = Message(content=code_text, role="Human", cause_by=type(todo))
        msg = Message(content="", role="", cause_by=type(todo))
        return msg

    async def _think(self) -> Action:
        news = self.rc.news[0]
        if news:
            if isinstance(news, Message) and news.role != "user":
                if is_json(news.content):
                    msg = json.loads(news.content)
                    if isinstance(msg, dict):
                        if "next_step" in msg and msg["next_step"] == 1:
                            self.set_todo(self.actions[3])
                            return self.rc.todo
                        elif "next_step" in msg and (
                                msg["next_step"] == 2 or msg["next_step"] == 4):
                            self.set_todo(self.actions[2])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 3:
                            self.set_todo(self.actions[1])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 7:
                            self.set_todo(self.actions[0])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 10:
                            self.set_todo(self.actions[4])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 5:
                            self.set_todo(self.actions[5])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 0:
                            self.set_todo(self.actions[6])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == -1:
                            self.set_todo(self.actions[7])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 100:
                            self.set_todo(self.actions[4])
                            return self.rc.todo

        self.set_todo(self.actions[7])
        return self.rc.todo


class Read_ha_state_response(Action):
    name: str = "Read_ha_state_response"

    run: ClassVar[callable]

    async def run(self, context: str, entity_id_list, ha_address, ha_port, token):

        entitis_states = []
        if entity_id_list and isinstance(entity_id_list, list):
            if len(entity_id_list) <= read_entity_limit:
                for entity_id in entity_id_list:
                    state_info = {}
                    entity_state = get_entity_state_rest_api(entity_id, ha_address, ha_port, token)
                    # entity_state = get_entity_state_in_cache(sensor)
                    if entity_state:
                        entitis_states.append(entity_state)
            else:
                all_entities = get_entity_states_rest_api(ha_address, ha_port, token)
                entitis_states = [entity for entity in all_entities if entity['entity_id'] in entity_id_list]

        return json.dumps(entitis_states, ensure_ascii=False)


class Classify_L2_R(Action):
    name: str = "Classify_L2_R"

    run: ClassVar[callable]

    CLASSIFY_PROMPT_TEMPLATE: str = """
    ## Constraints:
    - 当需要读取设备的历史状态或历史触发记录时，或者需要读取设备的实时状态或实时触发记录时，如果候选设备是人体传感器、门锁、无线开关这三者之一，则只能选择设备下状态为时间戳的实体，不能选择其他类型的实体。
    - indicator_light(指示灯)类型的实体只有用户输入中明确说明涉及此类实体时，才作为候选实体，否则进行选择。
    - 不选择indicator_light类型的实体。
    - "x key switch"类型的设备代表灯的开关，每个key控制一盏灯。
    - 不选择指示灯实体。    
    - 现在的时间是：{time}

    ## context
    <<<
    {context}
    >>>，
    上述对话中涉及的角色包括：
        1.Human，用户。
        2.Doorman，用户对话分类器，分析用户对话
        2.Interpreter，根据对话的分析结果，选择下一步的动作。
        3.Actuator，根据调度器的结果，执行对应的动作，包括读取设备实体数据历史、调用home assistant设备实体服务、向用户确认、读取设备实体状态、播放提示语音等。

    ## Role:
    home assistant资深专家

    ## Profile:
    - Language:中文
    - Description: 你是一个home assistant专家，通过与用户的对话给出建议，帮助他们访问和控制home assistant设备。

    ## Skill:

    ## Workflow:
    think step by step，结合home assistant settings，分析context，判断用户的需求或意图/潜在意图，根据分析结果选择以下选项中的一项，并按照选项中的要求根据OutputFormat输出相关内容：
    分支1: 判断是否还有不清楚的问题，如果是，则输出"next_step"为4，并在"question"详细输出需要确认的内容；如果否，则进入分支2。
    分支2: 判断用户需求是否属于以下情况的一种，如果不是，则进入分支3。分支2的选项包括以下：
        1. 读取设备实体的历史数据和历史触发记录。如果用户的需求明确涉及过去某段时间内的状态或活动，请选择此项。
        - 判断条件：用户询问的时间范围明显在过去。包括但不限于“昨天”、“上周”、“今天早上”、“今天上午”、“上个月”或具体的历史时间点。如果用户提到的时间点是今天但在当前时间之前，如“今天上午”，也归为过去时间。
        - 步骤：逐一遍历设备实体列表，如果候选设备是人体传感器、门锁、无线开关这三者之一，则只能选择设备下状态为时间戳的实体，不能选择其他的实体，找到与用户需求尽可能相关的设备实体，加入read_entity_list列表中。
        - 输出：next_step=1，read_entity_list，start_time和end_time。并输出"question"和"result"为"NA"。
        - 如果read_entity_list列表为空或逐一遍历设备实体列表，还是找不到与用户需求相关的设备实体，输出"next_step"为5，并在"question"详细输出找不到设备实体的疑问。
        7. 读取设备实体的实时状态和实时触发记录。如果用户需要了解当前状态或最近的活动，请选择此项。
        - 判断条件：用户询问的时间范围明显是当前或最近。包括但不限于“现在”、“刚刚”、“目前”或具体的当前时间点。如果用户提到的时间点是今天并且在当前时间之后，如“今天下午”或“今晚”，归为当前时间。
        - 步骤：逐一遍历设备实体列表，如果候选设备是人体传感器、门锁、无线开关这三者之一，则只能选择设备下状态为时间戳的实体，不能选择其他的实体，找到与用户需求可能相关的设备实体加入read_entity_list列表中。
        - 输出：next_step=7，read_entity_list。并输出"question"和"result"为"NA"。
        - 如果设read_entity_list列表为空，或逐一遍历设备实体列表，还是找不到与用户需求相关的设备实体，输出"next_step"为5，并在"question"详细输出找不到设备实体的疑问。
    分支3： 如果用户的输入和home assistant无关，则选择此项进入闲聊模式。此时如果你觉得需要联网搜索来获取准确答案，输出"next_step"为0；如果你觉得不需要搜索网页就可以回答，输出"next_step"为-1。

    ## OutputFormat:
    分析结果以json结构输出，json格式如下：
       ```json
       {{
            "next_step": 1,
            "read_entity_list": [""...],
            "start_time": "2024-07-30T00:00:00+08:00",
            "end_time": "2024-07-30T23:59:59+08:00",
            "question": "",
            "result": ""
        }}
       ```

    ##home assistant settings：
    设备实体所在的区域：{area_list}
    设备实体列表：格式为
    ***
    [{{"area name":[{{"device name":{{"description": "", "entities": [{{"entity id":{{"services":[{{"service":{{'option_name': [''...]}}}}..],"state":""}}}}...]}}}}...]}}...]
    ***，
    具体如下：
    @@@
    {entity_list}
    @@@。
    """

    async def run(self, context, ha_storage: HaStorage, area_id_list, area_list, entity_list):
        global entity_type
        now = datetime.now()
        classifier_prompt = self.CLASSIFY_PROMPT_TEMPLATE.format(context=context, entity_list=entity_list,
                                                                 area_list=area_list, time=now)
        logger.info(f"分类器提示词：{classifier_prompt}")
        rsp_classifier = await self._aask(classifier_prompt)
        logger.info(rsp_classifier)
        rsp_result = parse_jason_code(rsp_classifier)

        return rsp_result


class Classify_L2_W(Action):
    name: str = "Classify_L2_W"

    run: ClassVar[callable]

    CLASSIFY_PROMPT_TEMPLATE: str = """
    ## context
    <<<
    {context}
    >>>，
    上述对话中涉及的角色包括：
        1.Human，用户。
        2.Doorman，用户对话分类器，分析用户对话
        2.Interpreter，根据对话的分析结果，选择下一步的动作。
        3.Actuator，根据调度器的结果，执行对应的动作，包括读取设备实体数据历史、调用home assistant设备实体服务、向用户确认、读取设备实体状态、播放提示语音等。

    ## Role:
    home assistant资深专家

    ## Profile:
    - Language:中文
    - Description: 你是一个home assistant专家，通过与用户的对话给出建议，帮助他们访问和控制home assistant设备。

    ## Constraints:
    - 不选择indicator_light类型的实体。
    - "x key switch"类型的设备代表灯的开关，每个key控制一盏灯。
    - 不选择指示灯实体。
    - 现在的时间是：{time}

    ## Skill:

    ## Workflow:
    think step by step，分析context，判断用户的需求或意图/潜在意图，根据分析结果执行以下的步骤，并按照选项中的要求根据OutputFormat输出相关内容：
    分支1: 判断是否还有不清楚的问题，如果是，则输出"next_step"为4，并在"question"详细输出需要确认的内容；如果否，则进入分支2。
    分支2: 判断用户需求是否属于以下情况的一种，如果不是，则进入分支3。分支2的选项包括以下：
        3. 如果需要调用实体的服务且用户的需求明确不需要向用户确认或在对话历史中已经与用户确认过调用此服务，选择此项。
            - 在实体服务列表中首先找到与用户需求尽可能相关的设备。记录所有候选设备，并从这些设备的实体和服务中筛选出相关的实体和服务。生成备选设备列表和实体/服务列表。
            - 检查是否有设备遗漏，确保所有相关设备都被考虑在内。如果发现遗漏的设备，将其加入到`write_entity_list`。然后检查实体和服务是否遗漏，将遗漏的实体和服务加入到`write_entity_list`。输出`write_entity_list`，输出`next_step=3，并输出`question`和`result`为`NA`。
    分支3： 如果用户的输入和home assistant无关，则选择此项进入闲聊模式。此时如果你觉得需要联网搜索来获取准确答案，输出"next_step"为0；如果你觉得不需要搜索网页就可以回答，输出"next_step"为-1。

    ## OutputFormat:
    分析结果以json结构输出，json格式如下：
       ```json
       {{
            "next_step": 1,
            "write_entity_list": [
                {{
                    "device": "",
                    "entities": [
                        {{
                            "entity_id": "",
                            "service": "",
                            "option": ""
                        }}...
                    ]

                }}...
            ],
            "question": "",
            "result": ""
        }}
       ```

    ##home assistant settings：
    设备所在的区域：{area_list}

    实体服务列表：格式为
    ***
    [{{"area name":[{{"device name":{{"description": "", "entities": [{{"entity id":{{"services":[{{"service":{{'option_name': [''...]}}}}..],"state":""}}}}...]}}}}...]}}...]
    ***，
    具体如下：
    @@@
    {entity_list}
    @@@。
    """

    async def run(self, context, ha_storage: HaStorage, area_id_list, area_list, entity_list):
        global entity_type
        now = datetime.now()
        classifier_prompt = self.CLASSIFY_PROMPT_TEMPLATE.format(context=context, entity_list=entity_list,
                                                                 area_list=area_list, time=now)
        logger.info(f"分类器提示词：{classifier_prompt}")
        rsp_classifier = await self._aask(classifier_prompt)
        logger.info(rsp_classifier)
        rsp_result = parse_jason_code(rsp_classifier)

        return rsp_result


class Classify_L2_Auto(Action):
    name: str = "Classify_L2_Auto"

    run: ClassVar[callable]

    CLASSIFY_PROMPT_TEMPLATE: str = """
    ## Constraints:
    - 生成automation的yaml时，如果trigger相关设备是人体传感器、门锁、无线开关这三者之一，则只能选择设备下状态为时间戳的实体，不能选择其他的。此时触发状态为任意。
    - When generating the automation, specifically for the trigger, if the candidate device is a motion sensor, lock, or wireless switch, only select entities that support timestamp functionality. Use the timestamp as the triggering condition, and do not select other entities that lack this capability.
    - 不选择indicator_light类型的实体。
    - "x key switch"类型的设备代表灯的开关，每个key控制一盏灯。
    - 不选择指示灯实体。
    - 现在的时间是：{time}

    ## context
    <<<
    {context}
    >>>，
    上述对话中涉及的角色包括：
        1.Human，用户。
        2.Doorman，用户对话分类器，分析用户对话
        2.Interpreter，根据对话的分析结果，选择下一步的动作。
        3.Actuator，根据调度器的结果，执行对应的动作，包括读取设备实体数据历史、调用home assistant设备实体服务、向用户确认、读取设备实体状态、播放提示语音等。

    ## Role:
    home assistant资深专家

    ## Profile:
    - Language:中文
    - Description: 你是一个home assistant专家，通过与用户的对话给出建议，并适时使用jinja模板，帮助用户生成automation的yaml配置文件。    

    ## Skill:

    ## Workflow:
    think step by step，分析context，判断用户的需求或意图/潜在意图，根据分析结果执行以下的步骤，并按照选项中的要求根据OutputFormat输出相关内容：
    分支1: 判断是否还有不清楚的问题，如果是，则输出"next_step"为4，并在"question"详细输出需要确认的内容；如果否，则进入分支2。
    分支2: 判断用户需求是否需要生成home assistant的自动化，如果不是，则进入分支3。分支2的具体步骤包括以下：
        1：找到合适的实体来生成automation中的trigger，根据需要使用jinja模板。
            - 在实体服务列表中首先找到与用户需求尽可能相关的设备。记录所有候选设备，并从这些设备的实体和服务中筛选出相关的实体和服务。生成备选trigger。
            - 检查是否有设备遗漏，确保所有相关设备都被考虑在内。如果发现遗漏的设备，将其加入到备选trigger。然后检查实体和服务是否遗漏，将遗漏的实体和服务加入到备选trigger。
            - 根据用户的需求，生成automation的trigger。
        2：找到合适的实体来生成automation中的condition，根据需要使用jinja模板。
            - 在实体服务列表中首先找到与用户需求尽可能相关的设备。记录所有候选设备，并从这些设备的实体和服务中筛选出相关的实体和服务。生成备选condition。
            - 检查是否有设备遗漏，确保所有相关设备都被考虑在内。如果发现遗漏的设备，将其加入到备选condition。然后检查实体和服务是否遗漏，将遗漏的实体和服务加入到备选condition。
            - 根据用户的需求，生成automation的condition。
        3：找到合适的实体来生成automation中的action，根据需要使用jinja模板。
            - 在实体服务列表中首先找到与用户需求尽可能相关的设备。记录所有候选设备，并从这些设备的实体和服务中筛选出相关的实体和服务。生成备选action。
            - 检查是否有设备遗漏，确保所有相关设备都被考虑在内。如果发现遗漏的设备，将其加入到备选action。然后检查实体和服务是否遗漏，将遗漏的实体和服务加入到备选action。
            - 根据用户的需求，生成automation的action。
        4: 按照要求输出yaml配置
    分支3： 如果用户的输入和home assistant无关，则选择此项进入闲聊模式。此时如果你觉得需要联网搜索来获取准确答案，输出"next_step"为0；如果你觉得不需要搜索网页就可以回答，输出"next_step"为-1。

    ## OutputFormat:
    分析结果以yaml结构输出，yaml格式如下：
       ```yaml
        alias: ''
        description: ''
        trigger:
        condition: []
        action:
        mode: single
       ```

    ##home assistant settings:
    设备所在的区域：{area_list}

    实体服务列表：格式为
    ***
    [{{"area name":[{{"device name":{{"description": "", "entities": [{{"entity id":{{"services":[{{"service":{{'option_name': [''...]}}}}..],"state":""}}}}...]}}}}...]}}...]
    ***，
    具体如下：
    @@@
    {entity_list}
    @@@。

    ##examples:
    example：
        alias: 办公室温湿度播报
        description: 当人体传感器检测到有人时，小爱音箱语音播报办公室的当前的温湿度
        trigger:
          - platform: state
            entity_id:
              - sensor.lumi_bmgl01_da3b_trigger_at
            to: null
        condition: []
        action:
          - service: text.set_value
            target:
              entity_id: text.xiaomi_l05c_502a_play_text
            data:
              value: 办公室温度是32度
        mode: single

    example：
        alias: 办公室温湿度播报
        description: 当人体传感器检测到有人时，小爱音箱语音播报办公室的当前的温湿度
        trigger:
          - platform: state
            entity_id:
              - sensor.lumi_bmgl01_da3b_trigger_at
            to: null
        condition: []
        action:
          - service: switch.turn_on
            metadata: {{}}
            data: {{}}
            target:
              entity_id: switch.zimi_dhkg02_ff60_right_switch_service
        mode: single
    """

    async def run(self, context, ha_storage: HaStorage, area_id_list, area_list, entity_list):
        global entity_type
        now = datetime.now()
        classifier_prompt = self.CLASSIFY_PROMPT_TEMPLATE.format(context=context, entity_list=entity_list,
                                                                 area_list=area_list, time=now)
        logger.info(f"分类器提示词：{classifier_prompt}")
        rsp_classifier = await self._aask(classifier_prompt)
        logger.info(rsp_classifier)

        yaml = parse_yaml_code(rsp_classifier)

        result = {
            "next_step": 100,
            "yaml_list": yaml
        }

        return json.dumps(result, ensure_ascii=False)


class Automation_initialize(Action):
    name: str = "Automation_initialize"

    run: ClassVar[callable]

    AUTOMATION_PROMPT_TEMPLATE: str = """
    ## Constraints:
    - 生成automation的yaml时，如果trigger相关设备是人体传感器、门锁、无线开关这三者之一，则只能选择设备下状态为时间戳的实体，不能选择其他的。此时触发状态为任意。
    - When generating the automation, specifically for the trigger, if the candidate device is a motion sensor, lock, or wireless switch, only select entities that support timestamp functionality. Use the timestamp as the triggering condition, and do not select other entities that lack this capability.
    - 不选择indicator_light类型的实体。
    - "x key switch"类型的设备代表灯的开关，每个key控制一盏灯。
    - 不选择指示灯实体。    
    - 现在的时间是：{time}

    ## Profile:
    - Language:中文
    - Description: 你是一个精通home assistant automation的专家，通过与用户的对话给出建议，并适时使用jinja模板，帮助用户生成automation的yaml配置文件。    

    ## Workflow:
    我现在给出我家里home assistant中的设备，请根据这些设备，think step by step，发挥想象力，提出尽可能多的建议，帮我实现自动化场景。

    ## OutputFormat:
    结果以yaml结构输出，yaml格式如下：
       ```yaml
        alias: ''
        description: ''
        trigger:
        condition: []
        action:
        mode: single
       ```

    ##home assistant settings:
    设备所在的区域：{area_list}

    实体服务列表：格式为
    ***
    [{{"area name":[{{"device name":{{"description": "", "entities": [{{"entity id":{{"services":[{{"service":{{'option_name': [''...]}}}}..],"state":""}}}}...]}}}}...]}}...]
    ***，
    具体如下：
    @@@
    {entity_list}
    @@@。

    ##examples:
    example：
        alias: 办公室灯光控制
        description: 当人体传感器检测到有人时，打开一楼办公室右侧的灯
        trigger:
          - platform: state
            entity_id:
              - sensor.lumi_bmgl01_da3b_trigger_at
            to: null
        condition: []
        action:
          - service: switch.turn_on
            target:
              entity_id: switch.zimi_dhkg02_ff60_right_switch_service
        mode: single

    example：
        alias: 办公室温湿度播报
        description: 当办公室的门打开时，小爱音箱语音播报办公室的当前的温湿度
        trigger:
          - platform: state
            entity_id:
              - sensor.loock_v6_cdfc_timestamp
            to: null
        condition: []
        action:
          - service: text.set_value
            target:
              entity_id: text.xiaomi_l05c_502a_play_text
            data:
              value: 办公室温度是{{ states('sensor.zhimi_sa2_8225_indoor_temperature') }}度，湿度是{{states('sensor.zhimi_sa2_8225_relative_humidity') }}%
        mode: single
    """

    async def run(self, area_list, entity_list):
        global entity_type
        now = datetime.now()
        automation_prompt = self.AUTOMATION_PROMPT_TEMPLATE.format(entity_list=entity_list,
                                                                   area_list=area_list, time=now)
        logger.info(f"自动化初始化提示词：{automation_prompt}")
        rsp = await self._aask(automation_prompt)
        logger.info(rsp)

        yaml = parse_yaml_code(rsp)

        result = {
            "next_step": 100,
            "yaml_list": yaml
        }

        return json.dumps(result, ensure_ascii=False)


class Interpreter(Role):
    name: str = "Interpreter"
    profile: str = "Interpreter"

    _act: ClassVar[callable]
    get_memories: ClassVar[callable]
    _think: ClassVar[callable]

    def __init__(self, tts_callback, listen_callback, ha_storage: HaStorage, web_driver, **kwargs):
        super().__init__(**kwargs)

        self.tts_callback = tts_callback
        self.listen_callback = listen_callback
        self.ha_storage = ha_storage
        self.web_driver = web_driver

        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))

        self._watch([Classify_L1, Evaluate])
        self.set_actions(
            [PlayMedia, AskNewRequirement(self.tts_callback, self.listen_callback),
             Classify_L2_R(config=gpt4o_mini_llm),
             Classify_L2_W(config=gpt4o_mini_llm),
             Classify_L2_Auto(config=gpt4o_mini_llm), Automation_initialize(config=gpt4o_mini_llm),
             SearchWeb( self.tts_callback, config=gpt4o_mini_llm),
             Gossip(self.tts_callback, config=gpt4o_mini_llm), ])
        self._set_react_mode(react_mode=RoleReactMode.BY_ORDER.value)

    async def _act(self) -> Message:
        global areas, area_id_list, devices, entity_type, entities
        news = self.rc.news[0]
        logger.info(f"Interpreter news:{news}")
        todo = self.rc.todo
        context = self.get_memories()

        if news:
            if isinstance(news, Message) and news.role != "user":
                if is_json(news.content):
                    msg = json.loads(news.content)
                    if isinstance(msg, dict):
                        if "next_step" in msg and msg["next_step"] == 1:
                            code_text = await todo.run(context)

                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and (msg["next_step"] == 5 or msg["next_step"] == 9):
                            # self.rc.todo = AskNewRequirement(self.tts_callback)
                            try:
                                msg_content = news.content
                                if msg_content:
                                    if isinstance(msg_content, str):
                                        try:
                                            content_data = json.loads(msg_content)

                                            content = ""
                                            if "result" in content_data and content_data["result"] and content_data[
                                                "result"] != "NA" and content_data["result"] != "result/NA":
                                                content = content_data["result"]
                                            if "question" in content_data and content_data["question"] and content_data[
                                                "question"] != "NA" and content_data["question"] != "question/NA":
                                                content = content + "，" + content_data["question"]

                                            logger.info(f"AskNewRequirement content:{content}")
                                            code_text = await todo.run(content)

                                            # 已满足用户需求，提出新需求后清空历史对话记录
                                            for key, value in self.rc.env.roles.items():
                                                value.rc.memory.clear()
                                                value.rc.msg_buffer.pop_all()

                                            msg = Message(content=code_text, role="Human", cause_by=type(todo))
                                            return msg
                                        except json.JSONDecodeError as e:
                                            logger.info(f"Error decoding msg_content JSON: {e}")
                                    else:
                                        logger.info("msg content is not a JSON string, it's a dictionary:",
                                                    msg_content)
                                else:
                                    logger.info("Doorman key not found in message data.")
                            except json.JSONDecodeError as e:
                                logger.info(f"Error decoding message content JSON: {e}")
                        elif "next_step" in msg and msg["next_step"] == 2:
                            if "tips" in msg and msg["tips"] and msg["tips"] != "NA" and msg["tips"] != "tips/NA":
                                await self.tts_callback(msg["tips"])
                                # self.tts_callback.say_sync(msg["tips"])
                                logger.info(f"msg['tips']:{msg['tips']}")
                            # self.rc.todo = Classify_L2_R()
                            area_id_list = msg["area_ids"]
                            areas = self.ha_storage.get_areas_by_id_list(area_id_list)
                            entity_type = msg["entity type"]
                            entity_type = update_entity_type(entity_type)
                            entities = self.ha_storage.get_simplified_devices_entities_with_description_list(
                                msg["area_ids"], entity_type)
                            devices = self.ha_storage.get_simplified_device_list(msg["area_ids"], [])

                            code_text = await todo.run(context, self.ha_storage, area_id_list, areas, entities)

                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 3:
                            if "tips" in msg and msg["tips"] and msg["tips"] != "NA" and msg["tips"] != "tips/NA":
                                await self.tts_callback(msg["tips"])
                                # self.tts_callback.say_sync(msg["tips"])
                                logger.info(f"msg['tips']:{msg['tips']}")
                            # self.rc.todo = Classify_L2_W()
                            if "AskUserToConfirm" in news.cause_by or "Evaluate" in news.cause_by:
                                code_text = await todo.run(context, self.ha_storage, area_id_list, areas, entities)

                                msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                                return msg
                            else:
                                area_id_list = msg["area_ids"]
                                areas = self.ha_storage.get_areas_by_id_list(area_id_list)
                                entity_type = msg["entity type"]
                                entity_type = update_entity_type(entity_type)
                                entities = self.ha_storage.get_simplified_devices_entities_with_description_list(
                                    msg["area_ids"], entity_type)
                                devices = self.ha_storage.get_simplified_device_list(msg["area_ids"], [])

                                code_text = await todo.run(context, self.ha_storage, area_id_list, areas, entities)

                                msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                                return msg
                        elif "next_step" in msg and msg["next_step"] == 4:
                            # self.rc.todo = Classify_L2_Auto(self.tts_callback)
                            if "Evaluate" in news.cause_by:
                                code_text = await todo.run(context, self.ha_storage, area_id_list, areas, entities)

                                msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                                return msg
                            else:
                                area_id_list = msg["area_ids"]
                                areas = self.ha_storage.get_areas_by_id_list(area_id_list)
                                entity_type = msg["entity type"]
                                entity_type = update_entity_type(entity_type)
                                entities = self.ha_storage.get_simplified_devices_entities_with_description_list(
                                    msg["area_ids"], entity_type)
                                devices = self.ha_storage.get_simplified_device_list(msg["area_ids"], [])

                                code_text = await todo.run(context, self.ha_storage, area_id_list, areas, entities)

                                msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                                return msg
                        elif "next_step" in msg and msg["next_step"] == 10:
                            # self.rc.todo = Automation_initialize(self.tts_callback)
                            areas = self.ha_storage.get_all_areas()
                            entities = self.ha_storage.get_simplified_devices_entities_with_description_list(
                                [], [])
                            code_text = await todo.run(areas, entities)

                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 0:
                            # self.rc.todo = SearchWeb(self.tts_callback)
                            if "tips" in msg and msg["tips"] and msg["tips"] != "NA" and msg["tips"] != "tips/NA":
                                await self.tts_callback(msg["tips"])

                            code_text = await todo.run(context, self.web_driver)

                            # # 清空历史对话记录
                            # for key, value in self.rc.env.roles.items():
                            #     value.rc.memory.clear()
                            #     value.rc.msg_buffer.pop_all()

                            # msg = Message(content=code_text, role="Human", cause_by=type(todo))
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == -1:
                            # self.rc.todo = Gossip(self.tts_callback)
                            code_text = await todo.run(context)

                            # 清空历史对话记录
                            for key, value in self.rc.env.roles.items():
                                value.rc.memory.clear()
                                value.rc.msg_buffer.pop_all()

                            # msg = Message(content=code_text, role="Human", cause_by=type(todo))
                            msg = Message(content="", role="", cause_by=type(todo))
                            return msg

        # self.rc.todo = Gossip(self.tts_callback)
        code_text = await todo.run(context)

        # 清空历史对话记录
        for key, value in self.rc.env.roles.items():
            value.rc.memory.clear()
            value.rc.msg_buffer.pop_all()

        # msg = Message(content=code_text, role="Human", cause_by=type(todo))
        msg = Message(content="", role="", cause_by=type(todo))
        return msg

    async def _think(self) -> Action:
        news = self.rc.news[0]
        if news:
            if isinstance(news, Message) and news.role != "user":
                if is_json(news.content):
                    msg = json.loads(news.content)
                    if isinstance(msg, dict):
                        if "next_step" in msg and msg["next_step"] == 1:
                            self.set_todo(self.actions[0])
                            return self.rc.todo
                        elif "next_step" in msg and (msg["next_step"] == 5 or msg["next_step"] == 9):
                            self.set_todo(self.actions[1])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 2:
                            self.set_todo(self.actions[2])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 3:
                            self.set_todo(self.actions[3])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 4:
                            self.set_todo(self.actions[4])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 10:
                            self.set_todo(self.actions[5])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 0:
                            self.set_todo(self.actions[6])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == -1:
                            self.set_todo(self.actions[7])
                            return self.rc.todo
        self.set_todo(self.actions[7])
        return self.rc.todo

    def get_memories(self, k=0):
        context = self.rc.memory.get(k=k)

        context_str = ""
        if isinstance(context, list):
            for msg in context:
                if isinstance(msg, Message):
                    # if "Classify_L1" in msg.cause_by:
                    #     continue
                    context_str += "《《" + msg.__str__() + "》》,"
                else:
                    context_str += "《《" + json.dumps(msg, ensure_ascii=False) + "》》,"
        else:
            context_str = json.dumps(context, ensure_ascii=False)

        return context_str


class Classify_L1(Action):
    name: str = "Classify_L1"

    run: ClassVar[callable]

    CLASSIFY_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是一个精通home assistant的智能家居助手，你通过与用户的对话给出相应的建议来读取真实世界中home assistant的设备实体状态/控制设备/读取设备历史数据，

    think step by step，你需要按照以下的框架来仔细分析：
    分支1.如果用户的需求与媒体播放相关，则输出"next_step"为1；反之则进入分支2。
    分支2.判断用户的意图或潜在意图是否满足以下条件，如果答案为是，则输出以下内容：在"requirement analysis"中对用户的意图或需求详细的答复，在{areas}中选择相关的区域，可以留空，在["sensor","binary_sensor","climate","camera","switch","light","button","cover","fan","humidifier","lawn_mower","lock","media_player","remote","vacuum","valve","water_heater"]选择相关的实体类型，尽可能选更多的类型（），可以留空，并在"tips"中输出你准备要做的下一步动作；如果答案为否则进入step 3。
    1.Location Identification: Determine if the user’s intent, needs, or implied requirements involve a specific location such as home, office, or company. Look for direct mentions, indirect hints, or contextual clues that indicate the conversation is related to a particular location where Home Assistant devices are situated.
    2.Device and Service Identification: Assess whether the user's conversation involves specific devices or services from the list. Identify any direct or indirect references to devices or services and determine if the user's needs suggest accessing or interacting with these Home Assistant devices.
    选择以下选项之一：
    1.如果第一步需要先读取home assistant中的设备或设备的状态，则输出"next_step"为2。
    2.如果第一步需要先控制home  assistant中的设备，则输出"next_step"为3。
    3.如果第一步需要先新增或修改home assistant的自动化场景，则输出"next_step"为4。
    4.如果第一步需要先初始化home assistant的自动化场景，则输出"next_step"为10。
    5.如果你不能确定用户是第一步要先读取还是要先控制或者是自动化场景，你需要进一步确认，此时输出"next_step"为2。
    分支3.如果你觉得第一步需要先联网搜索来获取准确答案，输出"next_step"为0；反之则进入分支4。

    分析结果以json结构输出，json格式如下：
    ```json 
    {{"next_step":,"requirement analysis":"", "area_ids":[""...],"entity type":[""...], "tips":""}}
    ```。

    以下是用户和你的对话历史：<<<{user_input}>>>
    """

    async def run(self, context: str, areas):
        classifier_prompt = self.CLASSIFY_PROMPT_TEMPLATE.format(user_input=context, areas=areas)
        logger.info(f"分类器L1提示词：{classifier_prompt}")
        rsp_classifier = await self._aask(classifier_prompt)
        logger.info(rsp_classifier)
        rsp_result = parse_jason_code(rsp_classifier)

        return rsp_result


class Doorman(Role):
    name: str = "Doorman"
    profile: str = "Doorman"

    _act: ClassVar[callable]

    def __init__(self, ha_storage: HaStorage, **kwargs):
        super().__init__(**kwargs)

        self.areas = ha_storage.get_all_areas()

        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))

        self._watch([UserRequirement])
        self.set_actions([Classify_L1(config=gpt4o_mini_llm)])

    async def _act(self) -> Message:
        todo = self.rc.todo

        context = self.get_memories()
        if isinstance(context, list):
            context = str(context)
        code_text = await todo.run(context, self.areas)
        msg = Message(content=code_text, role=self.name, cause_by=type(todo))

        return msg
