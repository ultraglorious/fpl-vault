import time

import requests


class APIClient:
    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                last_exc = e
                if attempt < self.max_retries - 1:
                    delay = 2 ** attempt
                    print(f"  [RETRY] attempt {attempt + 1}/{self.max_retries} failed: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
        raise last_exc

    def get(self, endpoint: str, params=None, headers=None) -> dict:
        response = self._request_with_retry("GET", f"{self.base_url}{endpoint}", params=params, headers=headers)
        return response.json()

    def post(self, endpoint: str, json_data: dict, headers=None) -> dict:
        response = self._request_with_retry("POST", f"{self.base_url}{endpoint}", json=json_data, headers=headers)
        return response.json()

    def put(self, endpoint: str, json_data: dict, headers=None) -> dict:
        pass

    def delete(self, endpoint: str, headers=None) -> dict:
        pass
