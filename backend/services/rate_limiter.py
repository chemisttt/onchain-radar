import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

            if self.tokens >= 1:
                self.tokens -= 1
                return
            wait = (1 - self.tokens) / self.rate
            await asyncio.sleep(wait)
            self.tokens = 0
            self.last_refill = time.monotonic()


limiters: dict[str, TokenBucket] = {
    "dexscreener": TokenBucket(rate=5, capacity=5),
    "etherscan": TokenBucket(rate=5, capacity=5),
    "goplus": TokenBucket(rate=2, capacity=5),
    "honeypot": TokenBucket(rate=2, capacity=5),
    "rugcheck": TokenBucket(rate=1, capacity=3),
    "helius": TokenBucket(rate=5, capacity=10),
    "rpc": TokenBucket(rate=20, capacity=50),
}


async def acquire(name: str):
    bucket = limiters.get(name)
    if bucket:
        await bucket.acquire()
