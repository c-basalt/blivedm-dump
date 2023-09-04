import http.cookies

import aiohttp

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36'
)


def session_from_cookies(cookies: dict, timeout=10, domain='bilibili.com'):
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
    cookie_jar = http.cookies.SimpleCookie()
    for key, value in cookies.items():
        cookie_jar[key] = value
        cookie_jar[key]['domain'] = domain
    session.cookie_jar.update_cookies(cookie_jar)
    return session


async def validate_cookies(cookies):
    async with session_from_cookies(cookies) as session:
        async with session.get('https://api.bilibili.com/x/web-interface/nav') as r:
            if r.status == 200:
                if '{"code":0,' in (await r.text()):
                    return True
