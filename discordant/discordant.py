import asyncio
import json
import re
import sys
import traceback
from collections import namedtuple
from inspect import iscoroutinefunction
from os import path

import aiohttp
import discord

import discordant.utils as utils

Command = namedtuple('Command', ['name', 'arg_func', 'aliases', 'section', 'help'])


def decorate_all_events():
    def wrapper(cls):
        for name, obj in vars(cls).items():
            if iscoroutinefunction(obj) and name.startswith("on_"):
                setattr(cls, name, cls.event_dispatch(obj))
        return cls

    return wrapper


@decorate_all_events()
class Discordant(discord.Client):
    _CMD_NAME_REGEX = re.compile(r'[a-z0-9]+')
    _handlers = {}
    _commands = {}
    _aliases = {}
    _triggers = set()
    _events = {}

    def __init__(self, config_file='config.json'):
        super().__init__()

        self._token = ''
        self.command_char = ''
        self.config = {}
        self.commands_parsed = 0

        self.load_config(config_file)

    def run(self):
        super().run(self._token)

    def load_config(self, config_file):
        if utils.is_url(config_file):
            async def f():
                nonlocal config_file
                with aiohttp.ClientSession() as session:
                    async with session.get(config_file) as response:
                        self.config = await response.json()

            self.loop.run_until_complete(f())
        elif not path.exists(config_file):
            print("No config file found (expected '{}').".format(config_file))
            print("Copy config-example.json to", config_file,
                  "and edit it to use the appropriate settings.")
            sys.exit(-1)
        else:
            with open(config_file, "r") as f:
                self.config = json.load(f)
        self._token = self.config['login']['token']
        self.command_char = self.config['commands']['command_char']
        self.load_aliases()

    def load_aliases(self):
        if 'aliases' not in self.config:
            return
        aliases = self.config['aliases']
        for base_cmd, alias_list in aliases.items():
            cmd_name = self._aliases[base_cmd]

            for alias in alias_list:
                self._aliases[alias] = cmd_name
                self._commands[cmd_name].aliases.append(alias)

    async def on_error(self, event_method, *args, **kwargs):
        await super().on_error(event_method, *args, **kwargs)
        print("Error:\n" + utils.python_format(traceback.format_exc()), file=sys.stderr)

    async def on_ready(self):
        await self.change_presence(
            game=discord.Game(name=self.config["client"]["game"])
            if self.config["client"]["game"] else None)

    async def on_message(self, message):
        if message.content.startswith(self.command_char) and \
                        message.author != self.user:
            await self.run_command(message)
            return

        for handler_name, trigger in self._handlers.items():
            match = trigger.search(message.content)
            if match:
                await getattr(self, handler_name)(match, message)
            # for match in trigger.finditer(message.content):
            #     await getattr(self, handler_name)(match, message)
            # the spam potential of this is too high...

    async def run_command(self, message):
        split = message.content.split(None, 1)
        cmd_name = split[0][1:]
        args = split[1] if len(split) > 1 else ""

        if cmd_name in self._aliases:
            self.commands_parsed += 1
            cmd = self._commands[self._aliases[cmd_name]]
            params = [args, message]
            if cmd.arg_func:
                res = cmd.arg_func(args)
                if isinstance(res, tuple):
                    params[0] = res[1]
                    res = res[0]
                if not res:
                    await self.send_message(message.channel, cmd.help)
                    return
            await getattr(self, cmd.name)(*params)

    @classmethod
    def register_handler(cls, trigger, regex_flags=0):
        try:
            trigger = re.compile(trigger, regex_flags)
        except re.error as err:
            print('Invalid trigger "{}": {}'.format(trigger, err.msg))
            sys.exit(-1)

        if trigger.pattern in cls._triggers:
            print('Cannot reuse pattern "{}"'.format(trigger.pattern))
            sys.exit(-1)

        cls._triggers.add(trigger.pattern)

        def wrapper(func):
            if not iscoroutinefunction(func):
                print('Handler for trigger "{}" must be a coroutine'.format(
                    trigger.pattern))
                sys.exit(-1)

            func_name = '_trg_' + func.__name__
            # disambiguate the name if another handler has the same name
            while func_name in cls._handlers:
                func_name += '_'

            setattr(cls, func_name, func)
            cls._handlers[func_name] = trigger

        return wrapper

    @classmethod
    def register_command(cls, name, aliases=None, section=None, arg_func=None):
        if not aliases:
            aliases = [name]
        else:
            aliases.insert(0, name)

        def wrapper(func):
            if not iscoroutinefunction(func):
                print('Handler for command "{}" must be a coroutine'.format(
                    name))
                sys.exit(-1)
            if not func.__doc__:
                print('Missing documentation in command "{}"'.format(
                    name))
                sys.exit(-1)
            func_name = '_cmd_' + func.__name__
            while func_name in cls._commands:
                func_name += '_'

            setattr(cls, func_name, func)
            cls._commands[func_name] = Command(
                func_name, arg_func, aliases,
                section or func.__module__.split(".")[-1],
                utils.cmd_help_format(func.__doc__))
            # associate the given aliases with the command
            for alias in aliases:
                if alias in cls._aliases:
                    print('The alias "{}"'.format(alias),
                          'is already in use for command',
                          cls._aliases[alias][:5].strip('_'))
                    sys.exit(-1)
                if cls._CMD_NAME_REGEX.match(alias) is None:
                    print('The alias "{}"'.format(alias),
                          ('is invalid. Aliases must only contain lowercase'
                           ' letters or numbers.'))
                    sys.exit(-1)
                cls._aliases[alias] = func_name

        return wrapper

    @classmethod
    def register_event(cls, name):
        name = "on_" + name

        def wrapper(func):
            if not iscoroutinefunction(func):
                print('Handler for event "{}" must be a coroutine'.format(name))
                sys.exit(-1)

            async def dummy(self, *args, **kwargs):
                f = getattr(super(), name, None)
                if f:
                    return await f(*args, **kwargs)

            dummy.__name__ = name

            if name not in Discordant.__dict__:
                setattr(cls, name, cls.event_dispatch(dummy))

            func_name = '_evt_' + func.__name__
            while func_name in cls._commands:
                func_name += '_'

            setattr(cls, func_name, func)
            if name in cls._events:
                cls._events[name].append(func)
            else:
                cls._events[name] = [func]

        return wrapper

    @classmethod
    def event_dispatch(cls, func):
        async def wrapper(*args, **kwargs):
            # only add large timers/loops through events,
            # not in Discordant methods.
            await func(*args, **kwargs)
            func_name = func.__name__
            if func_name in cls._events:
                await asyncio.gather(
                    *[f(*args, **kwargs) for f in cls._events[func_name]])

        return wrapper
