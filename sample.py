# -*- coding: utf-8 -*-
import asyncio
import logging
import json
from typing import Optional

import aiohttp

from blivedm.clients import BLiveClient

# 直播间ID的取值看直播间URL
TEST_ROOM_IDS = [
    12235923,
    14327465,
    21396545,
    21449083,
    23105590,
]

try:
    with open('room_list.txt', 'rt') as f:
        TEST_ROOM_IDS = [int(i) for i in f.readlines()]
except Exception:
    pass

# 这里填一个已登录账号的cookie。不填cookie也可以连接，但是收到弹幕的用户名会打码，UID会变成0
SESSDATA = ''

session: Optional[aiohttp.ClientSession] = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


def load_cookies(fn):
    with open(fn, 'rt', encoding='utf-8') as f:
        return {cookie['name']: cookie['value'] for cookie in json.load(f)['cookie_info']['cookies']}


class BaseHandler():
    _CMD_CALLBACK_DICT = {}

    def handle(self, client, command: dict):
        cmd = command.get('cmd', '').split(':')[0]

        callback = self._CMD_CALLBACK_DICT.get(cmd)
        if callback is not None:
            callback(self, client, command)
        else:
            if cmd == 'DANMU_MSG':
                print(client.room_id, cmd, str(command['info'][1:])[:150])
            elif cmd in ['LOG_IN_NOTICE']:
                print(client.room_id, cmd, str(command)[:150])

    def on_stopped_by_exception(self, client, exception: Exception):
        client.start()


async def main(cookie_fn):
    await run_multi_clients(cookie_fn)


async def load_cookies_delay(client, cookie_fn):
    await asyncio.sleep(60)
    client.cookies = load_cookies(cookie_fn)


async def run_multi_clients(cookie_fn):
    """
    演示同时监听多个直播间
    """
    clients = [BLiveClient(room_id) for room_id in TEST_ROOM_IDS]
    handler = BaseHandler()
    for client in clients:
        client.set_handler(handler)
        # client.cookies = load_cookies(cookie_fn)
        # asyncio.ensure_future(load_cookies_delay(client, cookie_fn))
        client.start()

    try:
        await asyncio.gather(*(
            client.join() for client in clients
        ))
    finally:
        await asyncio.gather(*(
            client.stop_and_close() for client in clients
        ))


if __name__ == '__main__':
    import sys
    asyncio.run(main(sys.argv[1]))
