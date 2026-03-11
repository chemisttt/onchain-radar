import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    def __init__(self):
        self.etherscan_api_key = self._get("ETHERSCAN_API_KEY", default="")
        self.helius_api_key = self._get("HELIUS_API_KEY", default="")
        self.goplus_api_key = self._get("GOPLUS_API_KEY", default="")
        self.host = self._get("HOST", default="0.0.0.0")
        self.port = int(self._get("PORT", default="8000"))
        self.db_path = Path(__file__).parent / "data" / "radar.db"
        self.telegram_bot_token = self._get("TELEGRAM_BOT_TOKEN", default="")
        self.telegram_chat_id = self._get("TELEGRAM_CHAT_ID", default="")
        self.telegram_thread_id = int(self._get("TELEGRAM_THREAD_ID", default="0") or "0")

        # Trading (Hyperliquid)
        self.hl_wallet_key = self._get("HL_WALLET_KEY", default="")
        self.hl_enabled = self._get("HL_TRADING_ENABLED", default="false").lower() == "true"
        self.hl_alloc_pct = float(self._get("HL_ALLOC_PCT", default="20"))
        self.hl_leverage = int(self._get("HL_LEVERAGE", default="3"))
        self.hl_max_positions = int(self._get("HL_MAX_POSITIONS", default="10"))
        self.hl_hard_stop_pct = float(self._get("HL_HARD_STOP_PCT", default="8"))

    def _get(self, key: str, default: str | None = None) -> str | None:
        return os.getenv(key, default)


settings = Settings()
