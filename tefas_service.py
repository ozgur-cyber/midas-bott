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
            self.session.get("https://www.tefas.gov.tr/FonKarsilastirma.aspx", headers=self.headers, timeout=5)
        except Exception as e:
            logging.error(f"TEFAS Oturum Hatasi: {e}")

    def get_fund_info(self, code):
        code_clean = code.upper().strip()
        today = datetime.now()
        
        # Son 14 gunun verisini isteyelim
        start_date = (today - timedelta(days=14)).strftime("%d.%m.%Y")
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
                raw_data = json_data.get("data", [])
                if raw_data:
                    # TARIH milisaniyesine gore eskidenden yeniye KESIN SIRALAMA yapıyoruz
                    data = sorted(raw_data, key=lambda x: x.get("TARIH", 0))
                    
                    # En son tarihli (en guncel) veri
                    latest = data[-1]
                    price = float(latest.get("FIYAT", 0) or 0)
                    title = latest.get("FONUNVAN", code_clean)
                    
                    # Gunluk degisim hesabi (Bugun vs Bir onceki is gunu)
                    daily_return = 0.0
                    if len(data) >= 2:
                        prev_price = float(data[-2].get("FIYAT", 0) or 0)
                        if prev_price > 0:
                            daily_return = ((price - prev_price) / prev_price) * 100

                    class FundInfo:
                        pass
                    
                    info = FundInfo()
                    info.price = price
                    info.title = title
                    info.daily_return = round(daily_return, 2)
                    return info
        except Exception as e:
            logging.error(f"TEFAS API Cekme Hatasi ({code_clean}): {e}")

        class DummyInfo:
            price = 0.0
            title = code_clean
            daily_return = 0.0
            
        return DummyInfo()

    def get_fund_history(self, code, days=30):
        return []

tefas_service = TefasService()
