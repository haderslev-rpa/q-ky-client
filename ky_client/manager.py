from ky_client.functionality.borgere import BorgereClient
from ky_client.functionality.opgaveindbakke import OpgaveindbakkeClient

from .client import KYClient


class KYClientManager:
    borgere: BorgereClient

    def __init__(self, username: str, password: str, idp: str) -> None:
        self._client = KYClient(username, password, idp)
        self.borgere = BorgereClient(ky_client=self._client)
        self.opgaveindbakke = OpgaveindbakkeClient(ky_client=self._client)
