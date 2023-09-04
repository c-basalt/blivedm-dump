#!/usr/bin/env python3
import time
import json
import logging
import logging.handlers
import asyncio
from datetime import datetime
from typing import Dict
import sys

import aiofile

from blivedm.clients import BLiveClient
from blivedm.utils import validate_cookies


logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler('dumper.log', maxBytes=50_000_000, backupCount=3)
    ]
)


logger = logging.getLogger()


def get_date():
    return datetime.utcnow().strftime('%y%m%d')


class FileWriter:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.date = get_date()
        self._write_queue = asyncio.Queue()
        self._writer_future = None

    @property
    def fn(self):
        return f'{self.prefix}-{self.date}.jsonl'

    async def close(self):
        if self._writer_future:
            self._writer_future.cancel()
            try:
                await self._writer_future
            except asyncio.CancelledError:
                pass

    async def _writer(self):
        while True:
            try:
                self.date = get_date()
                async with aiofile.async_open(self.fn, 'at', encoding='utf-8') as afp:
                    while self.date == get_date():
                        to_write = await asyncio.wait_for(self._write_queue.get(), timeout=300)
                        await afp.write(to_write)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._writer_future = None
                break
            except Exception:
                logger.exception(f"Failed to write data to file {self.fn}")

    async def write(self, data: list):
        to_write = json.dumps(data, ensure_ascii=False) + '\n'
        await self._write_queue.put(to_write)
        if not self._writer_future:
            self._writer_future = asyncio.ensure_future(self._writer())


class DumpHandler:
    def __init__(self, prefix='', print=True) -> None:
        self._dump_queue = asyncio.Queue()
        self._writers: Dict[int, FileWriter] = {}
        self.prefix = prefix
        self._dispatcher_future = asyncio.ensure_future(self._dispatcher())

    @property
    def date(self):
        return datetime.utcnow().strftime('%y%m%d')

    async def close(self):
        self._dispatcher_future.cancel()
        try:
            await self._dispatcher_future
        except asyncio.CancelledError:
            pass
        await asyncio.gather(*[writer.close() for writer in self._writers.values()])

    async def _dispatcher(self):
        while True:
            try:
                room_id, data = await self._dump_queue.get()
                if room_id not in self._writers:
                    self._writers[room_id] = FileWriter(f'{self.prefix}{room_id}')
                await self._writers[room_id].write(data)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception('Failed to dispatch data to writer')

    def handle(self, client: BLiveClient, command: dict):
        cmd = command.get('cmd', '').split(':')[0]
        room_id = client.room_id or client.tmp_room_id
        self._dump_queue.put_nowait((room_id, [cmd, time.time(), command]))

        if cmd == 'DANMU_MSG':
            print(room_id, cmd, str(command['info'][1:])[:150])
        elif cmd in ['LOG_IN_NOTICE']:
            print(room_id, cmd, str(command)[:150])

    def on_stopped_by_exception(self, client: BLiveClient, exception: Exception):
        logger.error(f'room={client.room_id} client exited with error: {exception}')
        client.start()


async def load_cookies(cookie_fn):
    async with aiofile.async_open(cookie_fn, 'rt', encoding='utf-8') as afp:
        data = json.loads(await afp.read())
    if 'cookie_info' in data:
        return {cookie['name']: cookie['value'] for cookie in data['cookie_info']['cookies']}
    raise ValueError('Unknown cookie file format')


async def load_room_ids(room_fn: str):
    async with aiofile.async_open(room_fn, 'rt', encoding='utf-8') as afp:
        text: str = await afp.read()
    room_ids = set()
    for item in text.split():
        try:
            room_ids.add(int(item))
        except ValueError:
            pass
    return room_ids


class CookieLoader:
    def __init__(self, cookie_fn: str, rooms: Dict[int, BLiveClient]):
        self.cookie_fn = cookie_fn
        self.rooms = rooms

    async def init(self):
        self.cookies = await load_cookies(self.cookie_fn)
        self._runner_future = asyncio.ensure_future(self._runner())

    async def _runner(self):
        while True:
            try:
                cookies = await load_cookies(self.cookie_fn)
                if (await validate_cookies(cookies)):
                    self.cookies = cookies
                    for client in self.rooms.values():
                        client.cookies = cookies
                else:
                    logger.warning('cookies are invalid, skip updating')
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception('Failed to load cookies')
            await asyncio.sleep(3600)


async def main(room_fn, cookie_fn):
    login_handler = DumpHandler()
    guest_handler = DumpHandler('guest-')
    rooms: Dict[int, BLiveClient] = {}
    guest_rooms: Dict[int, BLiveClient] = {}

    cookie_loader = CookieLoader(cookie_fn, rooms)
    await cookie_loader.init()
    await load_room_ids(room_fn)

    while True:
        try:
            room_ids = await load_room_ids(room_fn)
            for room_id in room_ids - set(rooms):
                logger.info(f'Starting room={room_id}')
                rooms[room_id] = BLiveClient(room_id, cookies=cookie_loader.cookies)
                rooms[room_id].set_handler(login_handler)
                rooms[room_id].start()

                logger.info(f'Starting guest room={room_id}')
                guest_rooms[room_id] = BLiveClient(room_id)
                guest_rooms[room_id].set_handler(guest_handler)
                guest_rooms[room_id].start()
            for room_id in set(rooms) - room_ids:
                logger.info(f'Stopping room={room_id}')
                await rooms.pop(room_id).stop_and_close()
                await guest_rooms.pop(room_id).stop_and_close()
        except KeyboardInterrupt:
            await asyncio.gather(
                login_handler.close(),
                guest_handler.close(),
                *[room.stop_and_close() for room in rooms.values()],
                *[room.stop_and_close() for room in guest_rooms.values()],
            )
            break
        except Exception:
            logger.exception('Error while trying to load rooms')
        print('Running rooms:', rooms.keys(), guest_rooms.keys())
        await asyncio.sleep(60)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(sys.argv[0], '<room_list.txt>', '<cookies.json>')
    asyncio.run(main(*sys.argv[1:3]))
