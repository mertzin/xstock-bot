import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import requests

from logger_setup import setup_logger

logger = setup_logger()

KRAKEN_REST_BASE = "https://api.kraken.com"
KRAKEN_FUTURES_BASE = "https://futures.kraken.com/derivatives/api/v3"


class XStockClient:
    def __init__(self) -> None:
        self.api_key: str = os.getenv("KRAKEN_API_KEY", "")
        self.api_secret: str = os.getenv("KRAKEN_API_SECRET", "")
        self.paper_trade: bool = os.getenv("PAPER_TRADE", "false").lower() == "true"

    # ------------------------------------------------------------------ #
    # Auth helpers                                                         #
    # ------------------------------------------------------------------ #

    def _nonce(self) -> str:
        return str(int(time.time() * 1000))

    def _sign(self, url_path: str, data: Dict[str, str], nonce: str) -> str:
        post_data = urllib.parse.urlencode(data)
        encoded = (nonce + post_data).encode("utf-8")
        message = url_path.encode("utf-8") + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(self.api_secret)
        mac = hmac.new(secret, message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _private_post(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        nonce = self._nonce()
        params["nonce"] = nonce
        signature = self._sign(path, params, nonce)
        headers = {
            "API-Key": self.api_key,
            "API-Sign": signature,
        }
        url = KRAKEN_REST_BASE + path
        resp = requests.post(url, data=params, headers=headers, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise RuntimeError(f"Kraken API error on {path}: {payload['error']}")
        return payload.get("result", {})

    def _public_get(self, path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        url = KRAKEN_REST_BASE + path
        resp = requests.get(url, params=params or {}, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise RuntimeError(f"Kraken API error on {path}: {payload['error']}")
        return payload.get("result", {})

    # ------------------------------------------------------------------ #
    # Public endpoints                                                     #
    # ------------------------------------------------------------------ #

    def get_price(self, futures_symbol: str) -> Optional[float]:
        """Fetch indexPrice from Kraken Futures ticker."""
        try:
            url = f"{KRAKEN_FUTURES_BASE}/tickers/{futures_symbol}"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            ticker = data.get("ticker", {})
            price = ticker.get("indexPrice") or ticker.get("last")
            if price is None:
                logger.warning("No price in futures ticker for %s: %s", futures_symbol, data)
                return None
            return float(price)
        except Exception as exc:
            logger.error("get_price(%s) failed: %s", futures_symbol, exc)
            return None

    def get_pair_info(self, pair: str) -> Tuple[Optional[float], Optional[int]]:
        """Return (costmin, lot_decimals) for a tokenized asset pair."""
        try:
            result = self._public_get("/0/public/AssetPairs",
                                      {"aclass_base": "tokenized_asset"})
            info = result.get(pair) or result.get(pair + ".d")
            if info is None:
                logger.warning("Pair %s not found in AssetPairs", pair)
                return None, None
            costmin = float(info.get("costmin", 1.0))
            lot_decimals = int(info.get("lot_decimals", 8))
            return costmin, lot_decimals
        except Exception as exc:
            logger.error("get_pair_info(%s) failed: %s", pair, exc)
            return None, None

    # ------------------------------------------------------------------ #
    # Private endpoints                                                    #
    # ------------------------------------------------------------------ #

    def get_zusd_balance(self) -> Optional[float]:
        """Return ZUSD balance from Kraken account."""
        try:
            result = self._private_post("/0/private/Balance", {})
            zusd = result.get("ZUSD") or result.get("USD")
            if zusd is None:
                logger.warning("ZUSD not found in balance response: %s", result)
                return None
            return float(zusd)
        except Exception as exc:
            logger.error("get_zusd_balance() failed: %s", exc)
            return None

    def place_order(self, pair: str, side: str, volume: float) -> Optional[Dict[str, Any]]:
        """Place a market order. Logs only if PAPER_TRADE=true."""
        if self.paper_trade:
            logger.info("[PAPER] ORDER %s %s vol=%.6f", side.upper(), pair, volume)
            return {"txid": ["PAPER_TRADE"], "descr": {"order": f"paper {side}"}}

        try:
            params = {
                "ordertype": "market",
                "type": side.lower(),
                "pair": pair,
                "volume": f"{volume:.8f}",
            }
            result = self._private_post("/0/private/AddOrder", params)
            logger.info("Order placed: %s %s vol=%.6f txid=%s",
                        side.upper(), pair, volume, result.get("txid"))
            return result
        except Exception as exc:
            logger.error("place_order(%s %s %.6f) failed: %s", side, pair, volume, exc)
            return None
