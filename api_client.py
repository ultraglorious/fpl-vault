import requests

class APIClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def get(self, endpoint: str, params=None, headers=None) -> dict:
        response = requests.get(f"{self.base_url}{endpoint}", params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def post(self, endpoint: str, json_data: dict, headers=None) -> dict:
        response = requests.post(f"{self.base_url}{endpoint}", json=json_data, headers=headers)
        response.raise_for_status()
        return response.json()

    def put(self, endpoint: str, json_data: dict, headers=None) -> dict:
        pass

    def delete(self, endpoint: str, headers=None) -> dict:
        pass
