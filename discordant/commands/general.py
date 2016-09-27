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


@Discordant.register_command("help", ["info", "h", "cmds", "commands"],
                             context=True)
async def _help(self, args, message, context):
    """!help [command/section]
    displays command help and information."""
    sections = {}
    for cmd in self._commands.values():
        if cmd.perm_func and not cmd.perm_func(self, context.author):
            continue
        if cmd.section in sections:
            sections[cmd.section].append(cmd)
        else:
            sections[cmd.section] = [cmd]
    if args:
        cmd = utils.get_cmd(self, args)
        if cmd:
            if cmd.perm_func and not cmd.perm_func(self, context.author):
                await self.send_message(
                    message.channel,
                    "You are not authorized to use this command.")
                return
            await self.send_message(message.channel, cmd.help)
        elif args in sections:
            await utils.send_long_message(self, message.channel, _help_menu(
                {args: sections[args]}))
        else:
            await self.send_message(
                message.channel, "Command could not be found.")
    else:
        msg = None
        try:
            await utils.send_long_message(self, message.author, _help_menu(
                sections))
            await self.send_message(
                message.author,
                "**command help syntax**:\n"
                "  [] - optional argument\n"
                "  <> - required argument\n"
                "  \\* - any number of arguments\n"
                "  key=value - kwargs style argument (each key-value pair is "
                "separated by space, and the key and value are separated by the"
                " \"=\" character).\n"
                "  \\*\\* - any number of kwargs")
            await self.send_message(
                message.author,
                "Type !help [command/section] to display more information "
                "about a certain command or section.")
        except discord.errors.Forbidden:
            msg = await self.send_message(
                message.channel, "Please enable your PMs.")
        if message.server:
            if not msg:
                msg = await self.send_message(
                    message.channel, "Check your PMs.")
            await _delete_after(self, 5, [message, msg])


def _help_menu(sections):
    output = "**commands**:"
    for section, cmd_list in sections.items():
        tab_4 = " " * 4
        output += "\n  __{}__:\n".format(section) + \
                  "\n".join([tab_4 + "*{}* - ".format(cmd.aliases[0]) +
                             cmd.help.replace("\n", tab_4 + "\n").split(
                                 " - ", 1)[1] for cmd in cmd_list])
    return output


@Discordant.register_command("timezone", ["tz"], arg_func=utils.has_args)
async def _convert_timezone(self, args, message):
    """!timezone <time> <from> <\*to> or !timezone <\*timezone>
    displays time in given timezone(s)."""
    def get_timezone_by_code(code):
        code = code.upper()
        for tz_str in pytz.all_timezones:
            tz = timezone(tz_str)
            if tz.tzname(datetime.now()) == code:
                return tz
        raise ValueError(code + ": not a valid time zone code")

    def read_time(dt_str):
        formats = ["%I%p", "%I:%M%p", "%H", "%H:%M"]
        for f in formats:
            try:
                read_dt = datetime.strptime(dt_str, f)
                return datetime.now().replace(hour=read_dt.hour,
                                              minute=read_dt.minute)
            except ValueError:
                pass
        raise ValueError(dt_str + ": not a valid time format")

    def is_time(dt_str):
        try:
            read_time(dt_str)
        except ValueError:
            return False
        return True

    def relative_date_str(dt_1, dt_2):
        delta = dt_2.day - dt_1.day
        if delta > 1:
            delta = -1
        elif delta < -1:
            delta = 1
        return "same day" if delta == 0 else "1 day " + (
            "ahead" if delta > 0 else "behind")

    def dt_format(dt, tz_str, relative):
        new_dt = dt.astimezone(get_timezone_by_code(tz_str))
        return new_dt.strftime("%I:%M %p %Z") + (
            ", " + relative_date_str(dt, new_dt) if relative else "")

    split = args.split()
    try:
        is_t = is_time(split[0])
        if is_t:
            dt = get_timezone_by_code(split[1]).localize(read_time(split[0]))
            tz_strs = split[2:]
            output = "{} is{}".format(
                dt.strftime("%I:%M %p %Z"),
                ":\n" if len(tz_strs) > 1 else " ")
        else:
            dt = pytz.utc.localize(datetime.utcnow())
            tz_strs = split
            output = "It is currently" + (":\n" if len(tz_strs) > 1 else " ")
        output += "\n".join([dt_format(dt, tz_str, is_t) for tz_str in tz_strs])
        await self.send_message(message.channel, output)
    except ValueError:
        await self.send_message(message.channel, split[0] +
                                ": Not a valid time format or time zone code.")


def _dict_search_args_parse(args, keys=None):
    limit = 1
    query = args
    kwargs = {}
    if keys:
        kwargs = utils.get_kwargs(args, keys)
        query = utils.strip_kwargs(args, keys)
    result = re.match(r"^([0-9]+)\s+(.*)$", query)
    if result:
        limit, query = [result.group(x) for x in (1, 2)]
    return (int(limit), query) + ((kwargs,) if keys else ())


@Discordant.register_command("jisho", ["j"], arg_func=utils.has_args)
async def _jisho_search(self, args, message):
    """!jisho [limit] <query>
    searches japanese-english dictionary <http://jisho.org>."""
    search_args = _dict_search_args_parse(args)
    if not search_args:
        return
    limit, query = search_args
    if "#kanji" in query:
        await _jisho_kanji(self, limit, query, message)
        return
    url = "http://jisho.org/api/v1/search/words?keyword=" + \
          urllib.parse.quote(query, encoding="utf-8")
    try:
        with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()
    except Exception as e:
        await self.send_message(message.channel, "Request failed: " + str(e))
        return
    results = data["data"][:limit]
    if not results:
        await self.send_message(message.channel, "No results found.")
        return
    output = ""

    def display_word(obj, *formats):
        f = formats[len(obj) - 1]
        return f.format(*obj.values()) if len(obj) == 1 else f.format(**obj)

    for result in results:
        japanese = result["japanese"]
        output += display_word(japanese[0], "**{}**",
                               "**{word}** {reading}") + "\n"
        new_line = ""
        if "is_common" in result and result["is_common"]:
            new_line += "Common word. "
        if result["tags"]:  # it doesn't show jlpt tags, only wk tags?
            new_line += "Wanikani level " + ", ".join(
                [tag[8:] for tag in result["tags"]]) + ". "
        if new_line:
            output += new_line + "\n"
        senses = result["senses"]
        for index, sense in enumerate(senses):
            # jisho returns null sometimes for some parts of speech... k den
            parts = [x for x in sense["parts_of_speech"] if x is not None]
            if parts == ["Wikipedia definition"] and len(senses) > 1:
                continue
            if parts:
                output += "*" + ", ".join(parts) + "*\n"
            output += str(index + 1) + ". " + "; ".join(
                sense["english_definitions"])
            for attr in ["tags", "info"]:
                sense_attr = [x for x in sense[attr] if x]
                if sense_attr:
                    output += ". *" + "*. *".join(sense_attr) + "*"
            if sense["see_also"]:
                output += ". *See also*: " + ", ".join(sense["see_also"])
            output += "\n"
            output += "\n".join(
                ["{text}: {url}".format(**x) for x in sense["links"]])
        if len(japanese) > 1:
            output += "Other forms: " + ", ".join(
                [display_word(x, "{}", "{word} ({reading})") for x in
                 japanese[1:]]) + "\n"
        # output += "\n"
    await utils.send_long_message(
        self, message.channel, output, message.server is not None)


async def _jisho_kanji(self, limit, query, message):
    url = "http://jisho.org/search/" + urllib.parse.quote(
        query, encoding="utf-8")
    try:
        with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.text()
    except Exception as e:
        await self.send_message(message.channel, "Request failed: " + str(e))
        return
    tree = html.fromstring(data)
    info_div = tree.xpath('//div[@class="kanji details"]')
    if info_div:
        await self.send_message(message.channel, _jisho_kanji_info(tree))
        return
    results_div = tree.xpath('//div[@class="kanji_light_block"]')
    if not results_div:
        await self.send_message(message.channel, "No results found.")
        return
    results_divs = results_div[0].xpath(
        './div[@class="entry kanji_light clearfix"]')[:limit]
    output = ""
    for result_div in results_divs:
        k_url = result_div.xpath(
            'a[@class="light-details_link"]')[0].attrib["href"]
        try:
            with aiohttp.ClientSession() as session:
                async with session.get(k_url) as response:
                    k_data = await response.text()
        except Exception as e:
            output += "Request failed: {}, {}".format(k_url, e) + "\n"
            continue
        output += _jisho_kanji_info(html.fromstring(k_data)) + "\n"
    await self.send_message(message.channel, output.strip())


def _jisho_kanji_info(tree):
    details = tree.xpath('//div[@class="kanji details"]')[0]

    def remove_spaces(s, all=False):
        return re.sub(r"\s?", "", s) if all else re.sub(r"\s+", " ", s).strip()

    character = remove_spaces(
        details.xpath('//h1[@class="character"]')[0].text_content())
    meanings = remove_spaces(details.xpath(
        '//div[@class="kanji-details__main-meanings"]')[0].text_content())
    strokes = remove_spaces(details.xpath(
        '//div[@class="kanji-details__stroke_count"]')[0].text_content())
    stats_div = details.xpath('//div[@class="kanji_stats"]')[0]
    stats = " ".join([remove_spaces(x.text_content()) + "."
                      for x in stats_div.xpath('./div')])
    readings_div = details.xpath(
        '//div[@class="kanji-details__main-readings"]')[0]
    readings = "\n".join([remove_spaces(x.text_content()).replace("、", ",")
                          for x in readings_div])
    radicals_divs = [x[0] for x in details.xpath('//div[@class="radicals"]')]
    radical = remove_spaces(radicals_divs[0].text_content())
    parts_div = radicals_divs[1]
    parts = "Parts: " + ", ".join(
        remove_spaces(parts_div.xpath("./dd")[0].text_content(), True))
    return "**{}** {}\n*{}. {}*\n{}\n{}\n{}".format(
        character, meanings, strokes, stats, readings, radical, parts)


@Discordant.register_command("alc", arg_func=utils.has_args)
async def _alc_search(self, args, message):
    """!alc [limit] <query>
    searches english-japanese dictionary <http://alc.co.jp>."""
    search_args = _dict_search_args_parse(args)
    if not search_args:
        return
    limit, query = search_args
    url = "http://eow.alc.co.jp/search?q=" + \
          urllib.parse.quote(
              re.sub(r"\s+", "+", query), encoding="utf-8", safe="+")
    try:
        with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.text()
    except Exception as e:
        await self.send_message(message.channel, "Request failed: " + str(e))
        return
    output = ""
    tree = html.fromstring(data)
    results = tree.xpath('//div[@id="resultsList"]/ul/li')[:limit]
    if not results:
        await self.send_message(message.channel, "No results found.")
        return
    for result in results:
        words = [x for x in result.xpath('./span') if
                 x.attrib["class"].startswith("midashi")][0]
        highlight = " ".join(words.xpath('./h2/span[@class="redtext"]/text()'))
        output += re.sub("(" + highlight + ")", r"**\1**",
                         words.text_content()) + "\n"
        div = result.xpath('./div')[0]
        if div.xpath('./text()') or div.xpath('./span[@class="refvocab"]'):
            output += div.text_content()
        else:
            for br in div.xpath("*//br"):
                br.tail = "\n" + br.tail if br.tail else "\n"
            for element in div.xpath('./*'):
                if element.tag == "span":
                    if element.attrib["class"] == "wordclass":
                        output += element.text[1:-1] + "\n"
                    elif element.attrib["class"] == "attr":
                        # alc pls lmao.
                        output += element.text_content().strip().replace(
                            "＠", "カナ") + "\n"
                elif element.tag == "ol" or element.tag == "ul":
                    lis = element.xpath('./li')
                    if lis:
                        for index, li in enumerate(lis):
                            output += "{}. {}\n".format(
                                index + 1, li.text_content().strip())
                    else:
                        output += "1. " + element.text_content().strip() + "\n"
                # output += "\n"
        # cheap ass fuckers dont actually give 文例's
        # also removes kana things
        output = re.sub(r"(｛[^｝]*｝)|(【文例】)", "", output.strip()) + "\n"
    await utils.send_long_message(
        self, message.channel, output, message.server is not None)


async def _dict_search_link(self, match, message, cmd, group):
    await getattr(self, utils.get_cmd(self, cmd).name)(
        urllib.parse.unquote(match.group(group), encoding="utf-8"), message)


@Discordant.register_handler(r"http:\/\/jisho\.org\/(search|word)\/(\S*)")
async def _jisho_link(self, match, message):
    await _dict_search_link(self, match, message, "jisho", 2)


@Discordant.register_handler(r"http:\/\/eow\.alc\.co\.jp\/search\?q=([^\s&]*)")
async def _alc_link(self, match, message):
    await _dict_search_link(self, match, message, "alc", 1)


async def _example_sentence_search(self, args, message, cmd, url):
    search_args = _dict_search_args_parse(args, ["context"])
    if not search_args:
        return
    limit, query, kwargs = search_args
    context = kwargs["context"].lower() in ("true", "t", "yes", "y", "1") \
        if "context" in kwargs else False
    url = url + urllib.parse.quote(re.sub(r"\s+", "-", query), encoding="utf-8")
    try:
        with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.text()
    except Exception as e:
        await self.send_message(message.channel, "Request failed: " + str(e))
        return
    tree = html.fromstring(data)
    query = '//li[contains(@class, "sentence") and span[@class="the-sentence"]]'
    results = tree.xpath(query)[:limit]
    if not results:
        await self.send_message(message.channel, "No results found.")
        return
    japanese = cmd == "yourei"

    def sentence_text(element, class_prefix="the"):
        lst = element.xpath('span[@class="' + class_prefix + '-sentence"]')
        return ("" if japanese else " ").join(
            lst[0].xpath("text() | */text()")) if lst else ""

    def result_text(element):
        text = sentence_text(element)
        match = re.search(r'"([^"]*)"', tree.xpath("//script[1]/text()")[0])
        pattern = match.group(1).replace("\\\\", "\\")
        text = re.sub(pattern, r"**\1**", text, flags=re.I)
        if context:
            sentences = [x for x in [sentence_text(element, "prev"), text,
                                     sentence_text(element, "next")] if x]
            text = ("" if japanese else " ").join(sentences)
        return text

    await utils.send_long_message(
        self, message.channel,
        "\n".join([(str(index + 1) + ". " if len(
            results) > 1 else "") + result_text(result) for index, result in
                   enumerate(results)]),
        message.server is not None)


@Discordant.register_command("yourei", arg_func=utils.has_args)
async def _yourei_search(self, args, message):
    """!yourei [limit] <query> [context=bool]
    Searches Japanese example sentences from <http://yourei.jp>."""
    await _example_sentence_search(
        self, args, message, "yourei", "http://yourei.jp/")


@Discordant.register_command("nyanglish", arg_func=utils.has_args)
async def _nyanglish_search(self, args, message):
    """!nyanglish [limit] <query> [context=bool]
    searches english example sentences from <http://nyanglish.com>."""
    await _example_sentence_search(
        self, args, message, "nyanglish", "http://nyanglish.com/")


@Discordant.register_handler(r"http:\/\/(yourei\.jp|nyanglish\.com)\/(\S+)")
async def _yourei_link(self, match, message):
    await _dict_search_link(
        self, match, message, match.group(1).split(".")[0], 2)


async def _delete_after(self, time, args):
    await asyncio.sleep(time)
    f = getattr(
        self, "delete_message" + ("s" if isinstance(args, list) else ""))
    await f(args)


@Discordant.register_command("strokeorder", ["so"], arg_func=utils.has_args)
async def _stroke_order(self, args, message):
    """!strokeorder <character>
    shows stroke order for a kanji character."""
    file = str(ord(args[0])) + "_frames.png"
    url = "http://classic.jisho.org/static/images/stroke_diagrams/" + file
    try:
        with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 404:
                    await self.send_message(message.channel,
                                            args[0] + ": Kanji not found.")
                    return
                raw_response = await response.read()
    except Exception as e:
        await self.send_message(message.channel, "Request failed: " + str(e))
        return
    orig_image = Image.open(io.BytesIO(raw_response))
    image = _crop_and_shift_img(orig_image)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    await self.send_file(message.channel, buffer, filename=file)


def _crop_and_shift_img(img):
    char_width = 109  # width/height of one character
    chars_per_line = 4  # max before discord starts resizing it
    max_width = char_width * chars_per_line
    slices = int(math.ceil(img.width / max_width))
    total_height = char_width * slices
    width = min(max_width, img.width)
    new_img = Image.new("RGBA", (width, total_height), color=(0, 0, 0, 0))
    for i in range(slices):
        left = i * max_width
        right = min(left + max_width, img.width)
        _slice = img.crop((left, 0, right, char_width))
        new_img.paste(_slice, (0, char_width * i))
    return new_img


@Discordant.register_command("showvc", ["hidevc"], context=True)
async def _show_voice_channels_toggle(self, args, message, context):
    """!showvc
    toggles visibility to the #voice-\* text channels."""
    query = {"user_id": message.author.id}
    collection = self.mongodb.always_show_vc
    cursor = await collection.find(query).to_list(None)
    show = not cursor[0]["value"] if cursor else True
    await collection.update(
        query,
        dict(query, value=show),
        upsert=True)
    role = discord.utils.get(self.default_server.roles, name="VC Shown")
    if show:
        await self.add_roles(context.author, role)
        msg = await self.send_message(message.channel, ":white_check_mark:")
    else:
        if not context.author.voice_channel:
            await self.remove_roles(context.author, role)
        msg = await self.send_message(
            message.channel, ":negative_squared_cross_mark:")
    if message.server:
        await _delete_after(self, 5, [message, msg])


@Discordant.register_command("readingcircle", ["rc"], context=True)
async def _reading_circle(self, args, message, context):
    """!readingcircle <beginner/intermediate>
    add/remove yourself to ping notification lists for beginner or intermediate
    reading circles."""
    try:
        role_name = "Reading Circle " + args[0].upper() + args[1:].lower()
        role = discord.utils.get(context.server.roles, name=role_name)
        if role in context.author.roles:
            await self.remove_roles(context.author, role)
            msg = await self.send_message(
                message.channel, ":negative_squared_cross_mark:")
        else:
            await self.add_roles(context.author, role)
            msg = await self.send_message(message.channel, ":white_check_mark:")
    except (AttributeError, IndexError):
        msg = await self.send_message(message.channel, context.cmd.help)
    if message.server:
        await _delete_after(self, 5, [message, msg])


@Discordant.register_command("tag", ["t"], context=True)
async def _tag(self, args, message, context):
    """!tag <tag> [content/delete]
    display, add, edit, or delete tags (text stored in the bot's database)."""
    collection = self.mongodb.tags
    if not args:
        await self.send_message(
            message.channel,
            context.cmd.help + "\nTags: " + ", ".join(
                [x["tag"] for x in await collection.find().to_list(None)]))
        return
    split = args.split(None, 1)
    tag = split[0]
    content = split[1] if len(split) > 1 else None
    query = {"tag": tag}
    cursor = await collection.find(query).to_list(None)

    def has_permission(user):
        return cursor[0]["owner"] == user.id or \
               context.server.default_channel.permissions_for(
                   user).manage_messages

    if content == "delete":
        if not cursor:
            await self.send_message(message.channel, "Tag could not be found.")
            return
        if not has_permission(context.author):
            await self.send_message(
                message.channel, "You're not allowed to delete this tag.")
            return
        await collection.remove(query)
        await self.send_message(message.channel, "Deleted tag: " + tag)
    elif content:
        if not cursor:
            await collection.insert(
                {"tag": tag, "content": content, "owner": message.author.id})
            await self.send_message(message.channel, "Added tag: " + tag)
        else:
            if not has_permission(context.author):
                await self.send_message(
                    message.channel, "You're not allowed to edit this tag.")
                return
            await collection.update(query, {"$set": {"content": content}})
            await self.send_message(message.channel, "Updated tag: " + tag)
    else:
        await self.send_message(
            message.channel,
            cursor[0]["content"] if cursor else "Tag could not be found")


@Discordant.register_command("studying", context=True)
async def _studying(self, args, message, context):
    """!studying <resource>
    add/remove a studying resource tag to yourself."""
    collection = self.mongodb.studying_resources
    obj = await collection.find_one()
    if obj:
        role_names = obj["value"]
    else:
        await collection.insert({"value": []})
        role_names = []
    if not args:
        await self.send_message(
            message.channel,
            context.cmd.help + "\nResources: " + ", ".join(role_names))
        return
    split = args.split(None, 1)
    if len(split) > 1:
        subcmd = split[0]
        resource = split[1]
        role = discord.utils.get(context.server.roles, name=resource)
        if subcmd == "add":
            if not utils.is_controller(self, context.author):
                await self.send_message(
                    message.channel, "You're not allowed to do that.")
                return
            await collection.update({}, {"$push": {"value": resource}})
            if not role:
                await self.create_role(
                    context.server,
                    name=resource,
                    permissions=context.server.default_role.permissions,
                    mentionable=True)
            await self.send_message(
                message.channel, "Added studying resource: " + resource)
            return
        if subcmd == "del":
            if not utils.is_controller(self, context.author):
                await self.send_message(
                    message.channel, "You're not allowed to do that.")
                return
            await collection.update({}, {"$pull": {"value": resource}})
            if role:
                await self.delete_role(context.server, role)
            await self.send_message(
                message.channel, "Deleted studying resource: " + resource)
            return

    def search_str(s):
        return "".join(s.lower().split())

    def find_role(r):
        n = search_str(r.name)
        a = search_str(args)
        return n == a or n.startswith(a) or n in a

    roles = [discord.utils.get(context.server.roles, name=x) for x in role_names]
    role = discord.utils.find(find_role, roles)
    if not role:
        await self.send_message(message.channel, "Resource could not be found.")
        return
    if role in context.author.roles:
        await self.remove_roles(context.author, role)
        msg = await self.send_message(
            message.channel, ":negative_squared_cross_mark:")
    else:
        await self.add_roles(context.author, role)
        msg = await self.send_message(message.channel, ":white_check_mark:")
    if message.server:
        await _delete_after(self, 5, [message, msg])
