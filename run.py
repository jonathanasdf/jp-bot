#!/usr/bin/env python3
from discordant import Discordant


if __name__ == '__main__':
    bot = Discordant()
    try:
        bot.run()
    except KeyboardInterrupt:
        print('Exiting...')
