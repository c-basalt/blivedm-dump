# -*- coding: utf-8 -*-
import asyncio
import logging
from typing import Optional, List

import aiohttp

from . import ws_base
from .. import utils

__all__ = (
    'BLiveClient',
)

logger = logging.getLogger('blivedm')

UID_INIT_URL = 'https://api.bilibili.com/x/web-interface/nav'
BUVID_INIT_URL = 'https://t.bilibili.com/'
ROOM_INIT_URL = 'https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom'
DANMAKU_SERVER_CONF_URL = 'https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo'
DEFAULT_DANMAKU_SERVER_LIST = [
    {'host': 'broadcastlv.chat.bilibili.com', 'port': 2243, 'wss_port': 443, 'ws_port': 2244}
]


class BLiveClient(ws_base.WebSocketClientBase):
    """
    web端客户端

    :param room_id: URL中的房间ID，可以用短ID
    :param uid: B站用户ID，0表示未登录，None表示自动获取
    :param session: cookie、连接池
    :param heartbeat_interval: 发送心跳包的间隔时间（秒）
    """

    def __init__(
        self,
        room_id: int,
        *,
        session: Optional[aiohttp.ClientSession] = None,
        cookies: dict = {},
        heartbeat_interval=30,
    ):
        super().__init__(session, cookies=cookies, heartbeat_interval=heartbeat_interval)

        self._tmp_room_id = room_id
        """用来init_room的临时房间ID，可以用短ID"""
        self._uid: Optional[int] = None

        # 在调用init_room后初始化的字段
        self._room_owner_uid: Optional[int] = None
        """主播用户ID"""
        self._host_server_list: Optional[List[dict]] = None
        """
        弹幕服务器列表

        `[{host: "tx-bj4-live-comet-04.chat.bilibili.com", port: 2243, wss_port: 443, ws_port: 2244}, ...]`
        """
        self._host_server_token: Optional[str] = None
        """连接弹幕服务器用的token"""

    @property
    def tmp_room_id(self) -> int:
        """
        构造时传进来的room_id参数
        """
        return self._tmp_room_id

    @property
    def room_owner_uid(self) -> Optional[int]:
        """
        主播用户ID，调用init_room后初始化
        """
        return self._room_owner_uid

    @property
    def uid(self) -> Optional[int]:
        """
        当前登录的用户ID，未登录则为0，调用init_room后初始化
        """
        return self._uid

    async def init_room(self):
        """
        初始化连接房间需要的字段

        :return: True代表没有降级，如果需要降级后还可用，重载这个函数返回True
        """

        res = True
        if not await self._init_room_id_and_owner():
            res = False
            # 失败了则降级
            self._room_id = self._tmp_room_id
            self._room_owner_uid = 0

        if not await self._init_host_server():
            res = False
            # 失败了则降级
            self._host_server_list = DEFAULT_DANMAKU_SERVER_LIST
            self._host_server_token = None
        return res

    def reset_session(self):
        self._uid = None
        self._host_server_token = None
        self._host_server_list = None

    async def init_session(self):
        if self._uid is None:
            if not await self._init_uid():
                logger.warning('room=%d _init_uid() failed', self._tmp_room_id)

        if self._get_buvid() == '':
            if not await self._init_buvid():
                logger.warning('room=%d _init_buvid() failed', self._tmp_room_id)

        if not self._host_server_token:
            await self._init_host_server()

    async def _init_uid(self):
        if not self._get_cookie('SESSDATA'):
            # cookie都没有，不用请求了
            self._uid = 0
            return True

        try:
            async with self._session.get(
                UID_INIT_URL,
                headers={'User-Agent': utils.USER_AGENT},
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _init_uid() failed, status=%d, reason=%s', self._tmp_room_id,
                                   res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    if data['code'] == -101:
                        # 未登录
                        self._uid = 0
                        return True
                    logger.warning('room=%d _init_uid() failed, message=%s', self._tmp_room_id,
                                   data['message'])
                    return False

                data = data['data']
                if not data['isLogin']:
                    # 未登录
                    self._uid = 0
                else:
                    self._uid = data['mid']
                    logger.info(f"room={self._tmp_room_id} Continue as {data.get('uname')}({self._uid})")
                return True
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _init_uid() failed:', self._tmp_room_id)
            return False

    async def _init_buvid(self):
        try:
            async with self._session.get(
                BUVID_INIT_URL,
                headers={'User-Agent': utils.USER_AGENT},
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _init_buvid() status error, status=%d, reason=%s',
                                   self._tmp_room_id, res.status, res.reason)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _init_buvid() exception:', self._tmp_room_id)
        logger.info('room=%d _get_buvid() -> "%s"', self._tmp_room_id, self._get_buvid())
        return self._get_buvid() != ''

    async def _init_room_id_and_owner(self):
        try:
            async with self._session.get(
                ROOM_INIT_URL,
                headers={'User-Agent': utils.USER_AGENT},
                params={
                    'room_id': self._tmp_room_id
                },
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _init_room_id_and_owner() failed, status=%d, reason=%s', self._tmp_room_id,
                                   res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    logger.warning('room=%d _init_room_id_and_owner() failed, message=%s', self._tmp_room_id,
                                   data['message'])
                    return False
                if not self._parse_room_init(data['data']):
                    return False
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _init_room_id_and_owner() failed:', self._tmp_room_id)
            return False
        return True

    def _parse_room_init(self, data):
        room_info = data['room_info']
        self._room_id = room_info['room_id']
        self._room_owner_uid = room_info['uid']
        return True

    async def _init_host_server(self):
        try:
            async with self._session.get(
                DANMAKU_SERVER_CONF_URL,
                headers={'User-Agent': utils.USER_AGENT},
                params={
                    'id': self._room_id,
                    'type': 0
                },
            ) as res:
                if res.status != 200:
                    logger.warning('room=%d _init_host_server() failed, status=%d, reason=%s', self._room_id,
                                   res.status, res.reason)
                    return False
                data = await res.json()
                if data['code'] != 0:
                    logger.warning('room=%d _init_host_server() failed, message=%s', self._room_id, data['message'])
                    return False
                if not self._parse_danmaku_server_conf(data['data']):
                    return False
                logger.info('room=%d Loaded danmaku server info', self._room_id)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
            logger.exception('room=%d _init_host_server() failed:', self._room_id)
            return False
        return True

    def _parse_danmaku_server_conf(self, data):
        self._host_server_list = data['host_list']
        self._host_server_token = data['token']
        if not self._host_server_list:
            logger.warning('room=%d _parse_danmaku_server_conf() failed: host_server_list is empty', self._room_id)
            return False
        return True

    async def _on_network_coroutine_start(self):
        """
        在_network_coroutine开头运行，可以用来初始化房间
        """
        # 如果之前未初始化则初始化
        if self._host_server_token is None:
            if not await self.init_room():
                raise ws_base.InitError('init_room() failed')

    def _get_ws_url(self, retry_count) -> str:
        """
        返回WebSocket连接的URL，可以在这里做故障转移和负载均衡
        """
        host_server = self._host_server_list[retry_count % len(self._host_server_list)]
        logger.info(f"room={self._room_id} selecting server {host_server['host']}")
        return f"wss://{host_server['host']}:{host_server['wss_port']}/sub"

    async def _send_auth(self):
        """
        发送认证包
        """
        auth_params = {
            'uid': self._uid or 0,
            'roomid': self._room_id,
            'protover': 3,
            'buvid': self._get_buvid(),
            'platform': 'web',
            'type': 2,
        }
        if self._host_server_token is not None:
            auth_params['key'] = self._host_server_token
        logger.info(f"room={self._room_id} sending auth {auth_params} with cookies {list(self._session.cookie_jar)}")
        await self._websocket.send_bytes(self._make_packet(auth_params, ws_base.Operation.AUTH))
