#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
from pathlib import Path
from typing import AsyncIterator
import threading
from datetime import datetime, timedelta
import os
import fnmatch
import aiofiles
from typing import Callable, Optional, Union

from aiohttp import ClientSession, ClientTimeout
from miservice import MiAccount, MiIOService, MiNAService, miio_command
from rich import print
from rich.logging import RichHandler
import schedule

from mihagpt.bot import get_bot
from mihagpt.config import (
    COOKIE_TEMPLATE,
    LATEST_ASK_API,
    MI_ASK_SIMULATE_DATA,
    WAKEUP_KEYWORD,
    Config,
)
from mihagpt.tts import TTS, MiTTS, TetosTTS
from mihagpt.utils import detect_language, parse_cookie_string

from metagpt.tools.web_browser_engine import WebBrowserEngine
from metagpt.configs.browser_config import BrowserConfig
from metagpt.team import Team

from homeassistant.homeassistant_storage import HaStorage
from mihagpt.agents.ha_agent import Actuator, Judger, Interpreter, Doorman


EOF = object()


class MiGPT:

    browse_func: Union[Callable[[list[str]], None], None] = None
    web_browser_engine: Optional[WebBrowserEngine] = None

    def __init__(self, config: Config):
        self.config = config

        self.mi_token_home = Path.home() / ".mi.token"
        self.last_timestamp = int(time.time() * 1000)  # timestamp last call mi speaker
        self.cookie_jar = None
        self.device_id = ""
        self.parent_id = None
        self.mina_service = None
        self.miio_service = None
        self.in_conversation = False
        self.polling_event = asyncio.Event()
        self.last_record = asyncio.Queue(1)
        # setup logger
        self.log = logging.getLogger("xiaogpt")
        self.log.setLevel(logging.DEBUG if config.verbose else logging.INFO)
        self.log.addHandler(RichHandler())
        self.log.debug(config)
        self.mi_session = ClientSession()

        self.smart_mode = False
        self.xiaoai_mute = False
        self.xiaoai_mute_lock = threading.Lock()

        self.smart_mode_start_time = datetime.now()

        self.speaker_list = None
        self.current_speaker = None

        self.xiaomi_user_id_micoapi = ""
        self.xiaomi_sid_micoapi = ""
        self.xiaomi_service_token_micoapi = ""
        self.xiaomi_ssecurity_micoapi = ""

        self.xiaomi_user_id_miio = ""
        self.xiaomi_sid_miio = ""
        self.xiaomi_service_token_miio = ""
        self.xiaomi_ssecurity_miio = ""

        self.i = 0

    # 异步生成器
    async def string_to_async_iterator(self, text: str) -> AsyncIterator[str]:
        yield text

    def is_json(self, json_str):
        try:
            json_object = json.loads(json_str)
        except ValueError as e:
            return False
        return True

    def set_xiaoai_mute(self, value: bool):
        # 使用同步锁来保证线程安全
        with self.xiaoai_mute_lock:
            self.xiaoai_mute = value

    def get_xiaoai_mute(self) -> bool:
        # 使用同步锁来保证线程安全
        with self.xiaoai_mute_lock:
            return self.xiaoai_mute

    async def close(self):
        await self.mi_session.close()

    async def poll_latest_ask(self):
        async with ClientSession() as session:
            session._cookie_jar = self.cookie_jar
            while True:
                self.log.debug(
                    "Listening new message, timestamp: %s", self.last_timestamp
                )
                new_record = await self.get_latest_ask_from_xiaoai(session)
                start = time.perf_counter()
                self.log.debug(
                    "Polling_event, timestamp: %s %s", self.last_timestamp, new_record
                )
                await self.polling_event.wait()
                if self.smart_mode and self.current_speaker and self.current_speaker["use_command"]:
                    await self.stop_if_xiaoai_is_playing()
                elif (
                    self.config.mute_xiaoai
                    and new_record
                    and self.need_ask_gpt(new_record)
                ):
                    await self.stop_if_xiaoai_is_playing()
                if (d := time.perf_counter() - start) < 1:
                    # sleep to avoid too many request
                    self.log.debug("Sleep %f, timestamp: %s", d, self.last_timestamp)
                    # if you want force mute xiaoai, comment this line below.
                    await asyncio.sleep(1 - d)

    async def init_all_data(self):
        await self.login_miboy()
        await self._init_data_hardware()
        self.mi_session.cookie_jar.update_cookies(self.get_cookie(self.device_id))
        self.cookie_jar = self.mi_session.cookie_jar
        self.tts  # init tts

    async def login_miboy(self):
        account = MiAccount(
            self.mi_session,
            self.config.account,
            self.config.password,
            str(self.mi_token_home),
        )
        # Forced login to refresh to refresh token
        await account.login("micoapi")
        self.mina_service = MiNAService(account)
        self.miio_service = MiIOService(account)

    async def init_miboy(self):
        while True:
            if self.xiaomi_user_id_micoapi and self.xiaomi_sid_micoapi and self.xiaomi_service_token_micoapi and self.xiaomi_ssecurity_micoapi:
                account_micoapi = MiAccount(
                    self.mi_session,
                    self.xiaomi_user_id_micoapi,
                    self.xiaomi_sid_micoapi,
                    self.xiaomi_service_token_micoapi,
                    self.xiaomi_ssecurity_micoapi,
                    str(self.mi_token_home),
                    save_token=True,
                    update_token_callback=self.update_mi_token
                )
                self.mina_service = MiNAService(account_micoapi)
            if self.xiaomi_user_id_miio and self.xiaomi_sid_miio and self.xiaomi_service_token_miio and self.xiaomi_ssecurity_miio:
                account_miio = MiAccount(
                    self.mi_session,
                    self.xiaomi_user_id_miio,
                    self.xiaomi_sid_miio,
                    self.xiaomi_service_token_miio,
                    self.xiaomi_ssecurity_miio,
                    str(self.mi_token_home),
                    save_token=False,
                    update_token_callback=self.update_mi_token
                )
                self.miio_service = MiIOService(account_miio)
                break
            else:
                time.sleep(10)

    async def _init_data_hardware(self):
        if self.config.cookie:
            # if use cookie do not need init
            return
        hardware_data = await self.mina_service.device_list()
        # fix multi xiaoai problems we check did first
        # why we use this way to fix?
        # some videos and articles already in the Internet
        # we do not want to change old way, so we check if miotDID in `env` first
        # to set device id

        for h in hardware_data:
            if did := self.config.mi_did:
                if h.get("miotDID", "") == str(did):
                    self.device_id = h.get("deviceID")
                    break
                else:
                    continue
            if h.get("hardware", "") == self.config.hardware:
                self.device_id = h.get("deviceID")
                break
        else:
            raise Exception(
                f"we have no hardware: {self.config.hardware} please use `micli mina` to check"
            )
        if not self.config.mi_did:
            devices = await self.miio_service.device_list()
            try:
                self.config.mi_did = next(
                    d["did"]
                    for d in devices
                    if d["model"].endswith(self.config.hardware.lower())
                )
            except StopIteration:
                raise Exception(
                    f"cannot find did for hardware: {self.config.hardware} "
                    "please set it via MI_DID env"
                )

    def get_cookie(self, device_id):
        if self.config.cookie:
            cookie_jar = parse_cookie_string(self.config.cookie)
            # set attr from cookie fix #134
            cookie_dict = cookie_jar.get_dict()
            # 暂时注释
            # self.device_id = cookie_dict["deviceId"]
            return cookie_jar
        else:
            with open(self.mi_token_home) as f:
                user_data = json.loads(f.read())
            user_id = user_data.get("userId")
            service_token = user_data.get("micoapi")[1]
            cookie_string = COOKIE_TEMPLATE.format(
                device_id=device_id, service_token=service_token, user_id=user_id
            )
            return parse_cookie_string(cookie_string)

    @functools.cached_property
    def chatbot(self):
        return get_bot(self.config)

    async def simulate_xiaoai_question(self):
        data = MI_ASK_SIMULATE_DATA
        # Convert the data['data'] value from a string to a dictionary
        data_dict = json.loads(data["data"])
        # Get the first item in the records list
        record = data_dict["records"][0]
        # Replace the query and time values with user input
        record["query"] = input("Enter the new query: ")
        record["time"] = int(time.time() * 1000)
        # Convert the updated data_dict back to a string and update the data['data'] value
        data["data"] = json.dumps(data_dict)
        await asyncio.sleep(1)

        return data

    def need_ask_gpt(self, record):
        if not record:
            return False
        query = record.get("query", "")
        return (
            self.in_conversation
            and not query.startswith(WAKEUP_KEYWORD)
            or query.lower().startswith(tuple(w.lower() for w in self.config.keyword))
        )

    def need_change_prompt(self, record):
        query = record.get("query", "")
        return query.startswith(tuple(self.config.change_prompt_keyword))

    def _change_prompt(self, new_prompt):
        new_prompt = re.sub(
            rf"^({'|'.join(self.config.change_prompt_keyword)})", "", new_prompt
        )
        new_prompt = "以下都" + new_prompt
        print(f"Prompt from {self.config.prompt} change to {new_prompt}")
        self.config.prompt = new_prompt
        self.chatbot.change_prompt(new_prompt)

    async def get_latest_ask_from_xiaoai(self, session: ClientSession) -> dict | None:
        if not self.speaker_list or not isinstance(self.speaker_list, list):
            return None
        for speaker in self.speaker_list:
            if "involve" in speaker and speaker["involve"]:
                device_id = speaker["deviceID"]
                hardware = speaker["hardware"]

                self.mi_session.cookie_jar.update_cookies(self.get_cookie(device_id))
                self.cookie_jar = self.mi_session.cookie_jar

                retries = 3
                for i in range(retries):
                    try:
                        timeout = ClientTimeout(total=15)
                        r = await session.get(
                            LATEST_ASK_API.format(
                                hardware=hardware,
                                timestamp=str(int(time.time() * 1000)),
                            ),
                            timeout=timeout,
                        )
                    except Exception as e:
                        self.log.warning(
                            "Execption when get latest ask from xiaoai: %s", str(e)
                        )
                        continue
                    try:
                        data = await r.json()
                    except Exception:
                        self.log.warning("get latest ask from xiaoai error, retry")
                        if i == 1:
                            # tricky way to fix #282 #272 # if it is the third time we re init all data
                            print("Maybe outof date trying to re init it")
                            await self._retry()
                    else:
                        record = self._get_last_query(data)
                        print(f"record--------------------{record}")
                        if record:
                            self.current_speaker = speaker
                            self.device_id = speaker["deviceID"]
                            self.config.mi_did = speaker["miotDID"]
                            self.config.hardware = speaker["hardware"]
                            return record
                        else:
                            break
        return None

    async def _retry(self):
        await self.init_all_data()

    def _get_last_query(self, data: dict) -> dict | None:
        if d := data.get("data"):
            records = json.loads(d).get("records")
            if not records:
                return None
            last_record = records[0]
            timestamp = last_record.get("time")
            if timestamp > self.last_timestamp:
                try:
                    self.last_record.put_nowait(last_record)
                    self.last_timestamp = timestamp
                    return last_record
                except asyncio.QueueFull:
                    pass
        return None

    async def do_tts(self, use_command, device_id, mi_did, tts_command, value):
        if not use_command:
            try:
                await self.mina_service.text_to_speech(device_id, value)
            except Exception:
                pass
        else:
            await miio_command(
                self.miio_service,
                mi_did,
                f"{tts_command} {value}",
            )

    @functools.cached_property
    def tts(self) -> TTS:
        if self.config.tts == "mi":
            return MiTTS(self.config)
        else:
            return TetosTTS(self.config)

    async def wait_for_tts_finish(self):
        while True:
            if not await self.get_if_xiaoai_is_playing(self.current_speaker["deviceID"]):
                break
            await asyncio.sleep(1)

    @staticmethod
    def _normalize(message: str) -> str:
        message = message.strip().replace(" ", "--")
        message = message.replace("\n", "，")
        message = message.replace('"', "，")
        return message

    # 异步生成器
    async def string_to_async_iterator(self, text: str) -> AsyncIterator[str]:
        yield text

    async def speak_text(self, text: str):
        text_stream = self.string_to_async_iterator(text)

        await self.speak(text_stream)

    async def ask_gpt(self, query: str) -> AsyncIterator[str]:
        if not self.config.stream:
            if self.config.bot == "glm":
                answer = self.chatbot.ask(query, **self.config.gpt_options)
            else:
                answer = await self.chatbot.ask(query, **self.config.gpt_options)
            message = self._normalize(answer) if answer else ""
            yield message
            return

        async def collect_stream(queue):
            async for message in self.chatbot.ask_stream(
                query, **self.config.gpt_options
            ):
                await queue.put(message)

        def done_callback(future):
            queue.put_nowait(EOF)
            if future.exception():
                self.log.error(future.exception())

        self.polling_event.set()
        queue = asyncio.Queue()
        is_eof = False
        task = asyncio.create_task(collect_stream(queue))
        task.add_done_callback(done_callback)
        while True:
            if is_eof or not self.last_record.empty():
                break
            message = await queue.get()
            if message is EOF:
                break
            while not queue.empty():
                if (next_msg := queue.get_nowait()) is EOF:
                    is_eof = True
                    break
                message += next_msg
            if message:
                yield self._normalize(message)
        self.polling_event.clear()
        task.cancel()

    async def get_if_xiaoai_is_playing(self, device_id):
        playing_info = await self.mina_service.player_get_status(device_id)
        # WTF xiaomi api
        is_playing = (
            json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1)
            == 1
        )
        return is_playing

    async def mute_xiaoai(self, miio_service, mi_did, tts_command):
        if self.current_speaker and self.current_speaker["use_command"]:
            await miio_command(
                miio_service,
                mi_did,
                f"{tts_command} '_'",
            )
        else:
            # 获取本机ip
            await self.mina_service.play_by_url(self.device_id, "http://localhost/silent.MP3", 2)

    # 播放静音字符以便能够mute小爱音箱的原声，并判断是否完全没想要超过1分钟，超过的话就退出智能模式
    async def stop_if_xiaoai_is_playing(self):
        if self.get_xiaoai_mute():
            await self.mute_xiaoai(self.miio_service, self.config.mi_did, self.config.tts_command)
            print(f"------{self.i}:{self.xiaoai_mute},mi_did:{self.config.mi_did}")
            self.i += 1
            now = datetime.now()
            if abs(now - self.smart_mode_start_time) >= timedelta(seconds=30):
                self.smart_mode = False
                self.set_xiaoai_mute(False)
                # text_stream = self.string_to_async_iterator(f"现在退出智能模式了，用{'/'.join(self.config.keyword)}[/]字开头来唤醒我，重新进入智能模式吧")
                await self.speak_text(f"现在退出智能模式了，用{'/'.join(self.config.keyword)}[/]字开头来唤醒我，重新进入智能模式吧")

    async def wakeup_xiaoai(self):
        return await miio_command(
            self.miio_service,
            self.config.mi_did,
            f"{self.config.wakeup_command} {WAKEUP_KEYWORD} 0",
        )

    async def update_mi_token(self):
        # 找到home assistant下配置的小米auth文件，并解析token和ssecurity
        directory = self.config.ha_miot_auth_directory
        pattern_micoapi = 'auth-*cn-micoapi.json'
        pattern_xiaomiio = 'auth-*cn.json'
        for root, dirs, files in os.walk(directory):
            for filename in fnmatch.filter(files, pattern_micoapi):
                full_path = os.path.join(root, filename)
                try:
                    async with aiofiles.open(full_path, 'r', encoding='utf-8') as file:
                        content = await file.read()
                        xiaomi_auth_data = json.loads(content)
                        self.xiaomi_user_id_micoapi = xiaomi_auth_data["data"]["user_id"]
                        self.xiaomi_sid_micoapi = xiaomi_auth_data["data"]["sid"]
                        self.xiaomi_service_token_micoapi = xiaomi_auth_data["data"]["service_token"]
                        self.xiaomi_ssecurity_micoapi = xiaomi_auth_data["data"]["ssecurity"]
                        break
                except Exception as e:
                    print(f"无法读取文件 {full_path}: {e}")
                    return None
            for filename in fnmatch.filter(files, pattern_xiaomiio):
                full_path = os.path.join(root, filename)
                try:
                    async with aiofiles.open(full_path, 'r', encoding='utf-8') as file:
                        content = await file.read()
                        xiaomi_auth_data = json.loads(content)
                        self.xiaomi_user_id_miio = xiaomi_auth_data["data"]["user_id"]
                        self.xiaomi_sid_miio = xiaomi_auth_data["data"]["sid"]
                        self.xiaomi_service_token_miio = xiaomi_auth_data["data"]["service_token"]
                        self.xiaomi_ssecurity_miio = xiaomi_auth_data["data"]["ssecurity"]
                        break
                except Exception as e:
                    print(f"无法读取文件 {full_path}: {e}")
                    return None
        return self.xiaomi_user_id_micoapi, self.xiaomi_sid_micoapi, self.xiaomi_service_token_micoapi, self.xiaomi_ssecurity_micoapi, self.xiaomi_user_id_miio, self.xiaomi_sid_miio, self.xiaomi_service_token_miio, self.xiaomi_ssecurity_miio

    def schedule_task(self):
        # 计划任务每天早上8点执行
        schedule.every().day.at("08:00").do(self.update_mi_token)

        while True:
            now = datetime.now()
            # 找到下一个调度的任务时间
            next_run = schedule.next_run()
            if next_run:
                sleep_time = (next_run - now).total_seconds()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                schedule.run_pending()
            else:
                # 如果没有计划任务，则检查间隔可以稍微大一些
                time.sleep(60)

    async def run_forever(self, driver):
        await self.init_all_data()

        self.speaker_list = await self.mina_service.device_list()
        # 初始化HA设置
        ha_address = self.config.ha_address
        ha_port = "8123"
        ha_token = self.config.ha_token
        ha_storage = HaStorage()
        ha_storage.init_data(ha_address, ha_port, ha_token, self.speaker_list, force=True)

        browser = BrowserConfig()
        web_browser_engine = WebBrowserEngine.from_browser_config(
            browser,
            browse_func=self.browse_func,
        )

        self.team = Team()
        self.team.hire(
            [
                Judger(),
                Actuator(self.speak_text, None, ha_address, ha_port, ha_token, driver),
                Interpreter(self.speak_text, None, ha_storage, driver),
                Doorman(ha_storage),
            ]
        )
        task = asyncio.create_task(self.poll_latest_ask())
        assert task is not None  # to keep the reference to task, do not remove this
        print(
            f"Running xiaogpt now, 用[green]{'/'.join(self.config.keyword)}[/]来进入智能模式"
        )
        print(f"或用[green]{self.config.start_conversation}[/]开始持续对话")
        while True:
            self.polling_event.set()
            new_record = await self.last_record.get()
            self.polling_event.clear()  # stop polling when processing the question
            query = new_record.get("query", "").strip()

            if query == self.config.start_conversation:
                if not self.in_conversation:
                    print("开始对话")
                    self.in_conversation = True
                    await self.wakeup_xiaoai()
                await self.stop_if_xiaoai_is_playing()
                continue
            elif query == self.config.end_conversation:
                if self.in_conversation:
                    print("结束对话")
                    self.in_conversation = False
                await self.stop_if_xiaoai_is_playing()
                continue

            if self.smart_mode:
                print("-" * 20)
                print("问题：" + query + "？")

                self.set_xiaoai_mute(False)

                input = {
                    "user": query,
                    "current_area": self.current_speaker["area_name"]
                }

                try:
                    # await self.speak(self.ask_gpt(query))
                    self.team.invest(investment=100)
                    self.team.run_project(json.dumps(input, ensure_ascii=False))
                    await self.team.run(n_round=20)
                except Exception as e:
                    print(f"{self.chatbot.name} 回答出错 {str(e)}")
                # else:
                #     print("回答完毕")
                if self.in_conversation:
                    print(f"继续对话, 或用`{self.config.end_conversation}`结束对话")
                    await self.wakeup_xiaoai()

                self.set_xiaoai_mute(True)
                self.smart_mode_start_time = datetime.now()
            elif self.need_ask_gpt(new_record):
                await self.mute_xiaoai(self.miio_service, self.config.mi_did, self.config.tts_command)
                self.smart_mode = True

                query = re.sub(rf"^({'|'.join(self.config.keyword)})", "", query)
                print("-" * 20)
                print("问题：" + query + "？")

                self.set_xiaoai_mute(False)

                input = {
                    "user": query,
                    "current_area": self.current_speaker["area_name"]
                }

                try:
                    # await self.speak(self.ask_gpt(query))
                    self.team.invest(investment=100)
                    self.team.run_project(json.dumps(input, ensure_ascii=False))
                    await self.team.run(n_round=20)
                except Exception as e:
                    print(f"{self.chatbot.name} 回答出错 {str(e)}")
                else:
                    print("回答完毕")
                if self.in_conversation:
                    print(f"继续对话, 或用`{self.config.end_conversation}`结束对话")
                    await self.wakeup_xiaoai()

                self.set_xiaoai_mute(True)
                self.smart_mode_start_time = datetime.now()
                continue

    async def speak(self, text_stream: AsyncIterator[str]) -> None:
        first_chunk = await text_stream.__anext__()
        # Detect the language from the first chunk
        # Add suffix '-' because tetos expects it to exist when selecting voices
        # however, the nation code is never used.
        lang = detect_language(first_chunk) + "-"

        async def gen():  # reconstruct the generator
            yield first_chunk
            async for text in text_stream:
                yield text

        await self.tts.synthesize(lang, gen(), self.mina_service, self.miio_service, self.current_speaker["use_command"], self.current_speaker["deviceID"], self.current_speaker["miotDID"], self.current_speaker["tts_command"])
