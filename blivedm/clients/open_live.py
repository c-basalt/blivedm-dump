# -*- coding: utf-8 -*-
import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import random
from typing import *

import aiohttp

from . import ws_base

__all__ = (
    'OpenLiveClient',
)

logger = logging.getLogger('blivedm')

START_URL = 'https://live-open.biliapi.com/v2/app/start'
HEARTBEAT_URL = 'https://live-open.biliapi.com/v2/app/heartbeat'
END_URL = 'https://live-open.biliapi.com/v2/app/end'


class OpenLiveClient(ws_base.WebSocketClientBase):
    """
    开放平台客户端

    文档参考：https://open-live.bilibili.com/document/

    :param access_key: 在开放平台申请的access_key
    :param access_secret: 在开放平台申请的access_secret
    :param app_id: 在开放平台创建的项目ID
    :param room_owner_auth_code: 主播身份码
    :param session: cookie、连接池
    :param heartbeat_interval: 发送连接心跳包的间隔时间（秒）
    :param game_heartbeat_interval: 发送项目心跳包的间隔时间（秒）
    """

    def __init__(
        self,
        access_key: str,
        access_secret: str,
        app_id: int,
        room_owner_auth_code: str,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        heartbeat_interval=30,
        game_heartbeat_interval=20,
    ):
        super().__init__(session, heartbeat_interval)

        self._access_key = access_key
        self._access_secret = access_secret
        self._app_id = app_id
        self._room_owner_auth_code = room_owner_auth_code
        self._game_heartbeat_interval = game_heartbeat_interval

        # 在调用init_room后初始化的字段
        self._room_owner_uid: Optional[int] = None
        """主播用户ID"""
        self._host_server_url_list: Optional[List[str]] = []
        """弹幕服务器URL列表"""
        self._auth_body: Optional[str] = None
        """连接弹幕服务器用的认证包内容"""
        self._game_id: Optional[str] = None
        """项目场次ID，仅用于互动玩法类项目，其他项目为空字符串"""

        # 在运行时初始化的字段
        self._websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        """WebSocket连接"""
        self._network_future: Optional[asyncio.Future] = None
        """网络协程的future"""
        self._heartbeat_timer_handle: Optional[asyncio.TimerHandle] = None
        """发连接心跳包定时器的handle"""
        self._game_heartbeat_timer_handle: Optional[asyncio.TimerHandle] = None
        """发项目心跳包定时器的handle"""

    @property
    def room_owner_uid(self) -> Optional[int]:
        """
        主播用户ID，调用init_room后初始化
        """
        return self._room_owner_uid

    @property
    def room_owner_auth_code(self):
        """
        主播身份码
        """
        return self._room_owner_auth_code

    @property
    def app_id(self):
        """
        在开放平台创建的项目ID
        """
        return self._app_id

    @property
    def game_id(self) -> Optional[str]:
        """
        项目场次ID，仅用于互动玩法类项目，其他项目为空字符串，调用init_room后初始化
        """
        return self._game_id

    async def close(self):
        """
        释放本客户端的资源，调用后本客户端将不可用
        """
        if self.is_running:
            logger.warning('room=%s is calling close(), but client is running', self.room_id)

        if self._game_heartbeat_timer_handle is not None:
            self._game_heartbeat_timer_handle.cancel()
            self._game_heartbeat_timer_handle = None
        await self._end_game()

        await super().close()

    def _request_open_live(self, url, body: dict):
        body_bytes = json.dumps(body).encode('utf-8')
        headers = {
            'x-bili-accesskeyid': self._access_key,
            'x-bili-content-md5': hashlib.md5(body_bytes).hexdigest(),
            'x-bili-signature-method': 'HMAC-SHA256',
            'x-bili-signature-nonce': str(random.randint(0, 999999999)),
            'x-bili-signature-version': '1.0',
            'x-bili-timestamp': str(int(datetime.datetime.now().timestamp())),
        }

        str_to_sign = '\n'.join(
            f'{key}:{value}'
            for key, value in headers.items()
        )
        signature = hmac.new(
            self._access_secret.encode('utf-8'), str_to_sign.encode('utf-8'), hashlib.sha256
        ).hexdigest()
        headers['Authorization'] = signature

        headers['Content-Type'] = 'application/json'
        headers['Accept'] = 'application/json'
        return self._session.post(url, headers=headers, data=body_bytes)

    async def init_room(self):
        """
        开启项目，并初始化连接房间需要的字段

        :return: 是否成功
        """
        if not await self._start_game():
            return False

        if self._game_id != '' and self._game_heartbeat_timer_handle is None:
            self._game_heartbeat_timer_handle = asyncio.get_running_loop().call_later(
                self._game_heartbeat_interval, self._on_send_game_heartbeat
            )
        return True

    async def _start_game(self):
        try:
            async with self._request_open_live(
                START_URL,
                {'code': self._room_owner_auth_code, 'app_id': self._app_id}
            ) as res:
                if res.status != 200:
                    logger.warning('init_room() failed, status=%d, reason=%s', res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    logger.warning('init_room() failed, code=%d, message=%s, request_id=%s',
                                   data['code'], data['message'], data['request_id'])
                    return False
                if not self._parse_start_game(data['data']):
                    return False
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('init_room() failed:')
            return False
        return True

    def _parse_start_game(self, data):
        self._game_id = data['game_info']['game_id']
        websocket_info = data['websocket_info']
        self._auth_body = websocket_info['auth_body']
        self._host_server_url_list = websocket_info['wss_link']
        anchor_info = data['anchor_info']
        self._room_id = anchor_info['room_id']
        self._room_owner_uid = anchor_info['uid']
        return True

    async def _end_game(self):
        """
        关闭项目。互动玩法类项目建议断开连接时保证调用到这个函数（close会调用），否则可能短时间内无法重复连接同一个房间
        """
        if self._game_id in (None, ''):
            return True

        try:
            async with self._request_open_live(
                END_URL,
                {'app_id': self._app_id, 'game_id': self._game_id}
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _end_game() failed, status=%d, reason=%s',
                                   self._room_id, res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    logger.warning('room=%d _end_game() failed, code=%d, message=%s, request_id=%s',
                                   self._room_id, data['code'], data['message'], data['request_id'])
                    return False
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _end_game() failed:', self._room_id)
            return False
        return True

    def _on_send_game_heartbeat(self):
        """
        定时发送项目心跳包的回调
        """
        if not self.is_running:
            self._game_heartbeat_timer_handle = None
            return

        self._game_heartbeat_timer_handle = asyncio.get_running_loop().call_later(
            self._game_heartbeat_interval, self._on_send_game_heartbeat
        )
        asyncio.create_task(self._send_game_heartbeat())

    async def _send_game_heartbeat(self):
        """
        发送项目心跳包，仅用于互动玩法类项目
        """
        if self._game_id in (None, ''):
            logger.warning('game=%d heartbeat failed, game_id not found', self._game_id)
            return False

        try:
            async with self._request_open_live(
                HEARTBEAT_URL,
                {'game_id': self._game_id}
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _send_game_heartbeat() failed, status=%d, reason=%s',
                                   self._room_id, res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    logger.warning('room=%d _send_game_heartbeat() failed, code=%d, message=%s, request_id=%s',
                                   self._room_id, data['code'], data['message'], data['request_id'])
                    return False
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _send_game_heartbeat() failed:', self._room_id)
            return False
        return True

    async def _on_network_coroutine_start(self):
        """
        在_network_coroutine开头运行，可以用来初始化房间
        """
        # 如果之前未初始化则初始化
        if self._auth_body is None:
            if not await self.init_room():
                raise ws_base.InitError('init_room() failed')

    def _get_ws_url(self, retry_count) -> str:
        """
        返回WebSocket连接的URL，可以在这里做故障转移和负载均衡
        """
        return self._host_server_url_list[retry_count % len(self._host_server_url_list)]

    async def _send_auth(self):
        """
        发送认证包
        """
        auth_body = json.loads(self._auth_body)
        await self._websocket.send_bytes(self._make_packet(auth_body, ws_base.Operation.AUTH))
