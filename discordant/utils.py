import asyncio
import re
import shlex
from datetime import datetime

import discord


def split_every(s, n):
    return [s[i:i + n] for i in range(0, len(s), n)]


def is_url(s):  # good enough for now lmao
    return bool(re.match(r'^https?:\/\/.*', s))


def long_message(output, truncate=False, max_lines=15):
    output = output.strip()
    return ["\n".join(output.split("\n")[:max_lines]) +
            "\n... *Message truncated. " +
            "Send me a command over PM to show more!*"] \
        if truncate and output.count("\n") > max_lines \
        else split_every(output, 2000)

async def send_long_message(self, channel, message, truncate=False,
                            max_lines=15):
    for msg in long_message(message, truncate, max_lines):
        await self.send_message(channel, msg)


def get_kwargs(args_str, keys=None):
    return dict(
        x.split("=") for x in shlex.split(args_str)
        if "=" in x and
        (True if keys is None else x.split("=")[0] in keys))


def strip_kwargs(args_str, keys=None):
    return " ".join(
        [x for x in shlex.split(args_str)
         if not ("=" in x and
         (True if keys is None else x.split("=")[0] in keys))])


def get_from_kwargs(key, kwargs, default):
    return kwargs[key] if key in kwargs else default


def get_user(search, seq):
    def f(x):
        nonlocal search
        if search == x.mention:
            return True
        if search == str(x):
            return True
        if search == x.name or search == x.nick:
            return True
        temp = search.lower()
        if temp in x.name.lower() or (x.nick and temp in x.nick.lower()):
            return True
        return False

    return discord.utils.find(f, seq)


async def is_punished(self, member, *actions):
    punishments = ["ban", "warning", "mute"]
    if not set(actions) <= set(punishments):
        raise ValueError("Invalid action, must be one of: " +
                         ", ".join(punishments))
    cursor = await self.mongodb.punishments.find(
        {"user_id": member.id}).to_list(None)
    cursor.reverse()
    if not cursor:
        return False
    actions = actions if actions else punishments
    for action in actions:
        if await _is_punished(cursor, action):
            return True
    return False

async def _is_punished(cursor, action):
    if action == "ban":
        return bool(discord.utils.find(lambda x: x["action"] == "ban", cursor))
    else:
        def f(x):
            td = datetime.utcnow() - x["date"]
            return x["action"] == action and td.seconds / float(
                3600) + td.days * 24 < x["duration"]
        active = discord.utils.find(f, cursor)
        if not active:
            return False

        def g(x):
            nonlocal active
            xd = x["date"]
            ad = active["date"]
            td = xd - ad
            return x["action"] == "remove " + action and \
                (xd > ad and td.seconds / float(3600) + td.days * 24 < active[
                    "duration"])
        return discord.utils.find(g, cursor) is None


async def add_punishment_timer(self, member, action):
    role = action_to_role(self, action)
    while True:
        punished = await is_punished(self, member, action)
        if not punished:
            print("Removing punishment for " + str(member))
            await self.remove_roles(member, role)
            break
        await asyncio.sleep(
            self.config["moderation"]["punishment_check_rate"])


def action_to_role(self, action):
    dct = {
        "warning": "Warned",
        "mute": "Muted"
    }
    if action not in dct:
        raise ValueError("Invalid action, must be one of: " +
                         ", ".join(dct.keys()))
    return discord.utils.get(self.default_server.roles, name=dct[action])


def has_permission(user, permission):
    return len(
        [x for x in user.roles if getattr(x.permissions, permission)]) > 0


def get_cmd(self, cmd_name):
    return self._commands[self._aliases[cmd_name]]


def cmd_help_format(cmd):
    if isinstance(cmd, str):
        split = [s.strip() for s in cmd.split("\n")]
        return split[0] + " - " + " ".join(split[1:]).replace("\\n", "\n")
    else:
        return cmd.help


async def send_help(self, message, cmd_name):
    await self.send_message(message.channel, cmd_help_format(get_cmd(
        self, cmd_name)))