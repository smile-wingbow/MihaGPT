from typing import AsyncIterator

from miservice import MiIOService, MiNAService, miio_command

from mihagpt.config import Config
from mihagpt.tts.base import TTS
from mihagpt.utils import calculate_tts_elapse


class MiTTS(TTS):
    def __init__(
        self, config: Config
    ) -> None:
        super().__init__(config)

    async def say(self, text: str, mina_service, miio_service, use_command: bool, device_id: str, mi_did: str, tts_command: str) -> None:
        if not use_command:
            try:
                await mina_service.text_to_speech(device_id, text)
            except Exception:
                pass
        else:
            await miio_command(
                miio_service,
                mi_did,
                f"{tts_command} {text}",
            )

    async def synthesize(self, lang: str, text_stream: AsyncIterator[str], mina_service, miio_service, use_command: bool, device_id: str, mi_did: str, tts_command: str) -> None:
        async for text in text_stream:
            await self.say(text, mina_service, miio_service, use_command, device_id, mi_did, tts_command)
            await self.wait_for_duration(mina_service, calculate_tts_elapse(text), device_id)
