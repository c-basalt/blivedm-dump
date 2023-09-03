# -*- coding: utf-8 -*-
import asyncio
import random

from blivedm.client import BLiveClient

# 直播间ID的取值看直播间URL
TEST_ROOM_IDS = [
    12235923,
    14327465,
    21396545,
    21449083,
    23105590,
]


class BaseHandler():
    _CMD_CALLBACK_DICT = {}

    async def handle(self, client, command: dict):
        cmd = command.get('cmd', '').split(':')[0]

        if cmd not in self._CMD_CALLBACK_DICT:
            print(cmd, command)
        else:
            callback = self._CMD_CALLBACK_DICT[cmd]
            if callback is not None:
                await callback(self, client, command)


async def main():
    await run_single_client()
    await run_multi_clients()


async def run_single_client():
    """
    演示监听一个直播间
    """
    room_id = random.choice(TEST_ROOM_IDS)
    # 如果SSL验证失败就把ssl设为False，B站真的有过忘续证书的情况
    client = BLiveClient(room_id, ssl=True)
    handler = BaseHandler()
    client.add_handler(handler)

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
    clients = [BLiveClient(room_id) for room_id in TEST_ROOM_IDS]
    handler = BaseHandler()
    for client in clients:
        client.add_handler(handler)
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
