import asyncio
import io
import math
import re
import urllib.parse
from datetime import datetime

import aiohttp
import discord.game
import pytz
from PIL import Image
from lxml import html
from pytz import timezone

import discordant.utils as utils
from discordant import Discordant


@Discordant.register_handler(r"^(i|im|i'm)\b(?:(?!not).)*\bbored\b$", re.I)
async def _gotranslate(self, match, message):
    await self.send_message(message.channel, "Go translate!")
