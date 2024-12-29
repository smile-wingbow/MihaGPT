from __future__ import annotations

from mihagpt.bot.base_bot import BaseBot
from mihagpt.bot.chatgptapi_bot import ChatGPTBot
from mihagpt.bot.doubao_bot import DoubaoBot
from mihagpt.bot.gemini_bot import GeminiBot
from mihagpt.bot.glm_bot import GLMBot
from mihagpt.bot.langchain_bot import LangChainBot
from mihagpt.bot.llama_bot import LlamaBot
from mihagpt.bot.moonshot_bot import MoonshotBot
from mihagpt.bot.qwen_bot import QwenBot
from mihagpt.bot.yi_bot import YiBot
from mihagpt.config import Config

BOTS: dict[str, type[BaseBot]] = {
    "chatgptapi": ChatGPTBot,
    "glm": GLMBot,
    "gemini": GeminiBot,
    "qwen": QwenBot,
    "langchain": LangChainBot,
    "doubao": DoubaoBot,
    "moonshot": MoonshotBot,
    "yi": YiBot,
    "llama": LlamaBot,
}


def get_bot(config: Config) -> BaseBot:
    try:
        return BOTS[config.bot].from_config(config)
    except KeyError:
        raise ValueError(f"Unsupported bot {config.bot}, must be one of {list(BOTS)}")


__all__ = [
    "ChatGPTBot",
    "GLMBot",
    "GeminiBot",
    "MoonshotBot",
    "QwenBot",
    "get_bot",
    "LangChainBot",
    "DoubaoBot",
    "YiBot",
    "LlamaBot",
]
