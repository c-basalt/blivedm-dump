# -*- coding: utf-8 -*-
import asyncio
import http.cookies
import random
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


class BaseHandler():
    _CMD_CALLBACK_DICT = {}

    def handle(self, client, command: dict):
        cmd = command.get('cmd', '').split(':')[0]

        callback = self._CMD_CALLBACK_DICT.get(cmd)
        if callback is not None:
            callback(self, client, command)
        else:
            print(cmd, command)

    def on_stopped_by_exception(self, client, exception: Exception):
        client.start()


async def main():
    init_session()
    try:
        await run_single_client()
        await run_multi_clients()
    finally:
        await session.close()


def init_session():
    cookies = http.cookies.SimpleCookie()
    cookies['SESSDATA'] = SESSDATA
    cookies['SESSDATA']['domain'] = 'bilibili.com'

    global session
    session = aiohttp.ClientSession()
    session.cookie_jar.update_cookies(cookies)


async def run_single_client():
    """
    演示监听一个直播间
    """
    room_id = random.choice(TEST_ROOM_IDS)
    client = BLiveClient(room_id, session=session)
    handler = BaseHandler()
    client.set_handler(handler)

    client.start()
    try:
        # 演示5秒后停止
        await asyncio.sleep(5)
        client.stop()

        await client.join()
    finally:
        await client.stop_and_close()


async def run_multi_clients():
    """
    演示同时监听多个直播间
    """
    clients = [BLiveClient(room_id, session=session) for room_id in TEST_ROOM_IDS]
    handler = BaseHandler()
    for client in clients:
        client.set_handler(handler)
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
    asyncio.run(main())
