import requests
import logging
from datetime import datetime, timedelta

class TefasService:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.tefas.gov.tr",
            "Referer": "https://www.tefas.gov.tr/FonKarsilastirma.aspx"
        }
        self._init_session()

    def _init_session(self):
        try:
            # TEFAS'tan gerekli çerezleri alıyoruz
            self.session.get("https://www.tefas.gov.tr/FonKarsilastirma.aspx", headers=self.headers, timeout=5)
        except Exception as e:
            logging.error(f"TEFAS Oturum Hatasi: {e}")

    def get_fund_info(self, code):
        code_clean = code.upper().strip()
        today = datetime.now()
        
        # Son 7 gunun verisini isteyelim (Hafta sonu/tatil durumlarina karsi)
        start_date = (today - timedelta(days=7)).strftime("%d.%m.%Y")
        end_date = today.strftime("%d.%m.%Y")

        payload = {
            "fontip": "YAT",
            "fonkod": code_clean,
            "bastarih": start_date,
            "bittarih": end_date
        }

        try:
            url = "https://www.tefas.gov.tr/api/DB/BindHistoryInfo"
            res = self.session.post(url, data=payload, headers=self.headers, timeout=8)
            
            if res.status_code == 200:
                json_data = res.json()
                data = json_data.get("data", [])
                if data:
                    # En son tarihli kaydi alalim
                    last_data = data[0]
                    
                    class FundInfo:
                        pass
                    
                    info = FundInfo()
                    info.price = float(last_data.get("FIYAT", 0) or 0)
                    info.title = last_data.get("FONUNVAN", code_clean)
                    info.daily_return = float(last_data.get("GETIRI1D", 0) or 0)
                    return info
        except Exception as e:
            logging.error(f"TEFAS API Çekme Hatası ({code_clean}): {e}")

        # Başarısız olursa boş nesne döndür
        class DummyInfo:
            price = 0.0
            title = code_clean
            daily_return = 0.0
            
        return DummyInfo()

    def get_fund_history(self, code, days=30):
        return []

tefas_service = TefasService()
