import requests
import logging

class TefasService:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.tefas.gov.tr",
            "Referer": "https://www.tefas.gov.tr/FonKarsilastirma.aspx"
        }

    def get_fund_info(self, code):
        code_clean = code.upper().strip()
        
        # 1. Yöntem: Tefas Karşılaştırma Servisi
        try:
            url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
            # TEFAS tarih parametresi olmadan doğrudan aramayı destekler
            payload = {"fontip": "YAT", "fonkod": code_clean}
            res = requests.post(url, data=payload, headers=self.headers, timeout=8)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    last_data = data[0]
                    class FundInfo:
                        pass
                    info = FundInfo()
                    info.price = float(last_data.get("FIYAT", 0))
                    info.title = last_data.get("FONUNVAN", code_clean)
                    info.daily_return = float(last_data.get("GETIRI1D", 0) or 0)
                    return info
        except Exception as e:
            logging.error(f"TEFAS API1 hatasi: {e}")

        # 2. Yöntem: Fontur API Fallback
        try:
            url = f"https://fontur.com.tr/api/fon/{code_clean}"
            res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if data and "fiyat" in data:
                    class FundInfo:
                        pass
                    info = FundInfo()
                    info.price = float(data.get("fiyat", 0))
                    info.title = data.get("ad", code_clean)
                    info.daily_return = float(data.get("gunluk_getiri", 0))
                    return info
        except Exception as e:
            logging.error(f"TEFAS API2 hatasi: {e}")

        class DummyInfo:
            price = 0.0
            title = code_clean
            daily_return = 0.0
        return DummyInfo()

    def get_fund_history(self, code, days=30):
        return []

tefas_service = TefasService()
