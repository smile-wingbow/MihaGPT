from __future__ import annotations

import abc
import asyncio
import functools
import json
import logging
import os
import random
import socket
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from mihagpt.utils import get_hostname

if TYPE_CHECKING:
    from typing import TypeVar

    from miservice import MiNAService

    from mihagpt.config import Config

    T = TypeVar("T", bound="TTS")

logger = logging.getLogger(__name__)


class TTS(abc.ABC):
    """An abstract base class for Text-to-Speech models."""

    def __init__(
        self, config: Config
    ) -> None:
        self.config = config

    async def wait_for_duration(self, mina_service, duration: float, device_id: str) -> None:
        """Wait for the specified duration."""
        await asyncio.sleep(duration)
        while True:
            if not await self.get_if_xiaoai_is_playing(mina_service, device_id):
                break
            await asyncio.sleep(1)

    async def get_if_xiaoai_is_playing(self, mina_service, device_id: str):
        playing_info = await mina_service.player_get_status(device_id)
        # WTF xiaomi api
        is_playing = (
            json.loads(playing_info.get("data", {}).get("info", "{}")).get("status", -1)
            == 1
        )
        return is_playing

    @abc.abstractmethod
    async def synthesize(self, lang: str, text_stream: AsyncIterator[str], mina_service, miio_service, use_command: bool, device_id: str, mi_did: str, tts_command: str) -> None:
        """Synthesize speech from a stream of text."""
        raise NotImplementedError


class HTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.debug(f"{self.address_string()} - {format}", *args)

    def log_error(self, format, *args):
        logger.error(f"{self.address_string()} - {format}", *args)

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (socket.error, ConnectionResetError, BrokenPipeError):
            # ignore this or TODO find out why the error later
            pass


class AudioFileTTS(TTS):
    """A TTS model that generates audio files locally and plays them via URL."""

    def __init__(
        self, config: Config
    ) -> None:
        super().__init__(config)
        self.dirname = tempfile.TemporaryDirectory(prefix="xiaogpt-tts-")
        self._start_http_server()

    @abc.abstractmethod
    async def make_audio_file(self, lang: str, text: str) -> tuple[Path, float]:
        """Synthesize speech from text and save it to a file.
        Return the file path and the duration of the audio in seconds.
        The file path must be relative to the self.dirname.
        """
        raise NotImplementedError

    async def synthesize(self, lang: str, text_stream: AsyncIterator[str], mina_service, miio_service, use_command: bool, device_id: str, mi_did: str, tts_command: str) -> None:
        queue: asyncio.Queue[tuple[str, float]] = asyncio.Queue()
        finished = asyncio.Event()

        async def worker():
            async for text in text_stream:
                path, duration = await self.make_audio_file(lang, text)
                url = f"http://{self.hostname}:{self.port}/{path.name}"
                await queue.put((url, duration))
            finished.set()

        task = asyncio.create_task(worker())

        while True:
            try:
                url, duration = queue.get_nowait()
            except asyncio.QueueEmpty:
                if finished.is_set():
                    break
                else:
                    await asyncio.sleep(0.1)
                    continue
            logger.debug("Playing URL %s (%s seconds)", url, duration)
            await asyncio.gather(
                mina_service.play_by_url(device_id, url, _type=1),
                self.wait_for_duration(mina_service, duration, device_id),
            )
        await task

    def _start_http_server(self):
        # set the port range
        port_range = range(8050, 8090)
        # get a random port from the range
        self.port = int(os.getenv("XIAOGPT_PORT", random.choice(port_range)))
        # create the server
        handler = functools.partial(HTTPRequestHandler, directory=self.dirname.name)
        httpd = ThreadingHTTPServer(("", self.port), handler)
        # start the server in a new thread
        server_thread = threading.Thread(target=httpd.serve_forever)
        server_thread.daemon = True
        server_thread.start()

        self.hostname = get_hostname()
        logger.info(f"Serving on {self.hostname}:{self.port}")
