import json
import time
import uuid
from typing import Union, Dict

import requests
from loguru import logger

from config import KYBERSWAP_TOKENS
from utils.gas_checker import check_gas
from utils.helpers import retry, telegram
from .account import Account


class KyberSwap(Account):
    def __init__(self, account_id: int, private_key: str, proxy: Union[None, str]) -> None:
        super().__init__(account_id=account_id, private_key=private_key, proxy=proxy, chain="zksync")

    @retry
    @check_gas
    @telegram
    async def swap_with_aggregator(self,
                                   from_token: str,
                                   to_token: str,
                                   min_amount: float,
                                   max_amount: float,
                                   decimal: int,
                                   slippage: int,
                                   all_amount: bool,
                                   min_percent: int,
                                   max_percent: int):

        amount_wei, amount, balance = await self.get_amount(
            from_token,
            min_amount,
            max_amount,
            decimal,
            all_amount,
            min_percent,
            max_percent
        )

        client_id = uuid.uuid4().hex

        url = f"https://aggregator-api.kyberswap.com/{self.chain}/api/v1/routes"
        headers = \
            {
                "x-client-id": client_id
            }

        # Attention, NATIVE ETH must have address : "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
        params = {
            "chain": self.chain,
            "amountIn": amount_wei,
            "to": self.address,
            "tokenIn": KYBERSWAP_TOKENS[from_token],
            "tokenOut": KYBERSWAP_TOKENS[to_token],
            "saveGas": 1
        }
        result = requests.get(url, params=params, headers=headers, timeout=20).json()

        url = f"https://aggregator-api.kyberswap.com/{self.chain}/api/v1/route/build"
        headers = \
            {
                "x-client-id": client_id
            }
        deadline = int(time.time()) + 1000000
        body = {}
        body.update({"routeSummary": result["data"]["routeSummary"]})
        body.update({"deadline": deadline})
        # his is the amount of slippage the user can accept for his trade. The unit is bip.
        # The value is in ranges [0, 2000], 10 means 0.1%.
        body.update({"slippageTolerance": slippage * 100})
        body.update({"sender": self.address})
        body.update({"recipient": self.address})

        result = requests.post(url, data=json.dumps(body), headers=headers, timeout=20).json()

        swap_data = result["data"]["data"]
        router = result["data"]["routerAddress"]
        gas = result["data"]["gas"]
        logger.debug(f"estimated gas by kyberswap {gas}")
        if from_token == "ETH":
            eth_input_amount = amount_wei
        else:
            await self.approve(amount_wei, KYBERSWAP_TOKENS[from_token], router)
            eth_input_amount = 0

        transaction = {
            "chainId": await self.w3.eth.chain_id,
            "gasPrice": await self.w3.eth.gas_price,
            "from": self.w3.to_checksum_address(self.address),
            "to": self.w3.to_checksum_address(router),
            "data": swap_data,
            "value": eth_input_amount,
            "nonce": await self.w3.eth.get_transaction_count(self.address),
        }

        signed_txn = await self.sign(transaction)
        txn_hash = await self.send_raw_transaction(signed_txn)
        await self.wait_until_tx_finished(txn_hash.hex())
