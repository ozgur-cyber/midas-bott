import requests
import logging

class TefasService:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def get_fund_info(self, code):
        try:
            url = f"https://fontur.com.tr/api/fon/{code.upper()}"
            res = requests.get(url, headers=self.headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                class FundInfo:
                    pass
                info = FundInfo()
                info.price = float(data.get("fiyat", 0))
                info.title = data.get("ad", code)
                info.daily_return = float(data.get("gunluk_getiri", 0))
                return info
        except Exception as e:
            logging.error(f"TEFAS çekme hatası: {e}")
        
        class DummyInfo:
            price = 0.0
            title = code
            daily_return = 0.0
        return DummyInfo()

    def get_fund_history(self, code, days=30):
        return []

tefas_service = TefasService()
