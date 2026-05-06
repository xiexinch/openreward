import asyncio
import time
from dataclasses import dataclass
from typing import Literal, Optional

import aiohttp
from openreward.api._session.http import request_retryable


@dataclass
class ErrorResponse:
    type: Literal["error"]
    message: str


async def ping(url: str, sid: str, api_key: Optional[str], sleep_time: float, client: aiohttp.ClientSession, deployment: Optional[str] = None) -> None:
    while True:
        start = time.monotonic()
        await request_retryable(
            client,
            "POST",
            url,
            sid=sid,
            deployment=deployment,
            expect_json=False,
            token=api_key,
        )
        elapsed = time.monotonic() - start
        delay = max(0, sleep_time - elapsed)
        await asyncio.sleep(delay)
