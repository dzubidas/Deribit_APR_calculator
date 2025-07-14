import requests
import time
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
import logging
import sys
import os

# Configuration
CONFIG = {
    "CREDENTIALS_FILE": os.path.join(os.path.dirname(__file__), "service-account-key.json"),
    "SHEET_NAME": "Deribit IR calculator",
    "WORKSHEET_NAME": "Sheet2",
    "UPDATE_INTERVAL": 10,
    "LOG_LEVEL": "WARNING",  # Add this line
    "CURRENCIES": ["BTC", "ETH"]
}

class DeribitMultiTracker:
    def __init__(self):
        self.base_url = "https://www.deribit.com/api/v2/public"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'DeribitTracker/2.0'})
        
        # Setup logging
        logging.basicConfig(
            level=getattr(logging, CONFIG["LOG_LEVEL"]),
            format='%(asctime)s - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        self.logger = logging.getLogger(__name__)
        
        # Setup Google Sheets
        self.setup_sheets()
        
    def setup_sheets(self):
        """Initialize Google Sheets connection"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_file(CONFIG["CREDENTIALS_FILE"], scopes=scope)
            self.gc = gspread.authorize(creds)
            self.sheet = self.gc.open(CONFIG["SHEET_NAME"])
            
            # Use single worksheet for both currencies
            try:
                self.worksheet = self.sheet.worksheet(CONFIG["WORKSHEET_NAME"])
                self.logger.warning(f"Using existing worksheet: {CONFIG['WORKSHEET_NAME']}")
            except gspread.WorksheetNotFound:
                self.worksheet = self.sheet.add_worksheet(title=CONFIG["WORKSHEET_NAME"], rows=100, cols=10)
                self.logger.warning(f"Created new worksheet: {CONFIG['WORKSHEET_NAME']}")
            
            self.logger.warning("Connected to Google Sheets")
            
        except Exception as e:
            self.logger.error(f"Sheets connection failed: {e}")
            sys.exit(1)
    
    def api_request(self, endpoint, params=None):
        """Single method for all API requests"""
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", params=params, timeout=8)
            response.raise_for_status()
            data = response.json()
            
            if endpoint == "get_index_price":
                return data.get("result", {})
            else:
                return data.get("result", [])
                
        except requests.RequestException as e:
            self.logger.error(f"API error {endpoint}: {e}")
            return {} if endpoint == "get_index_price" else []
        except Exception as e:
            self.logger.error(f"Parse error {endpoint}: {e}")
            return {} if endpoint == "get_index_price" else []
    
    def calculate_funding_rate(self, mark_price, index_price):
        """Calculate funding rate using Deribit's method with damping"""
        if not mark_price or not index_price or index_price == 0:
            return None
            
        try:
            # Calculate premium rate: ((Mark - Index) / Index) * 100
            premium_rate = ((mark_price - index_price) / index_price) * 100
            
            # Apply damping: MAX(0.025, premium) + MIN(-0.025, premium)  
            damped_premium = max(0.025, premium_rate) + min(-0.025, premium_rate)
            
            # Convert to decimal (from percentage)
            funding_rate = damped_premium / 100
            
            return funding_rate
            
        except Exception as e:
            self.logger.error(f"Funding calculation error: {e}")
            return None
    
    def calculate_apr(self, mark_price, index_price, expiration_ms):
        """Calculate APR using Deribit formula"""
        if not index_price or expiration_ms <= 0:
            return 0.0
        
        minutes_left = (expiration_ms - datetime.now(timezone.utc).timestamp() * 1000) / 60000
        if minutes_left <= 0:
            return 0.0
        
        return ((mark_price / index_price) - 1) * 525600 / minutes_left * 100
    
    def format_time(self, expiration_ms):
        """Format time to expiration"""
        if expiration_ms <= 0:
            return "-"
        
        minutes_left = int((expiration_ms - datetime.now(timezone.utc).timestamp() * 1000) / 60000)
        if minutes_left <= 0:
            return "0d 0h 0m"
        
        days, hours, mins = minutes_left // 1440, (minutes_left % 1440) // 60, minutes_left % 60
        return f"{days}d {hours}h {mins}m"
    
    def fetch_currency_data(self, currency):
        """Fetch and process data for a specific currency (BTC or ETH)"""
        try:
            currency_lower = currency.lower()
            perpetual_name = f"{currency}-PERPETUAL"
            index_name = f"{currency_lower}_usd"
            
            # Fetch all data for this currency
            instruments_raw = self.api_request("get_instruments", {
                "currency": currency, 
                "kind": "future", 
                "expired": "false"
            })
            
            if not instruments_raw:
                self.logger.error(f"No {currency} instruments data")
                return []
            
            instruments = {i["instrument_name"]: i["expiration_timestamp"] for i in instruments_raw}
            
            futures_data = self.api_request("get_book_summary_by_currency", {
                "currency": currency, 
                "kind": "future"
            })
            
            if not futures_data:
                self.logger.error(f"No {currency} futures data")
                return []
            
            perpetual_data = self.api_request("get_book_summary_by_instrument", {
                "instrument_name": perpetual_name
            })
            
            index_data = self.api_request("get_index_price", {"index_name": index_name})
            
            if not index_data or "index_price" not in index_data:
                self.logger.error(f"No {currency} index price data")
                return []
            
            index_price = index_data["index_price"]
            
            # Calculate funding rate for perpetual
            calculated_funding_rate = None
            if perpetual_data and len(perpetual_data) > 0:
                perp_mark = perpetual_data[0].get("mark_price", 0)
                calculated_funding_rate = self.calculate_funding_rate(perp_mark, index_price)
            
            contracts = []
            
            # Add perpetual first
            if perpetual_data and len(perpetual_data) > 0:
                perp = perpetual_data[0]
                mark_price = perp.get("mark_price", 0)
                estimated_delivery = perp.get("estimated_delivery_price", 0)
                
                contracts.append({
                    "apr": "-",
                    "instrument": perpetual_name,
                    "bid": perp.get("bid_price", 0),
                    "mark": mark_price,
                    "ask": perp.get("ask_price", 0),
                    "percent_premium": ((mark_price - estimated_delivery) / estimated_delivery) * 100 if estimated_delivery else 0,
                    "dollar_premium": mark_price - estimated_delivery,
                    "settlement": "-",
                    "funding": calculated_funding_rate
                })
            
            # Process futures (exclude perpetual)
            futures_list = []
            for contract in futures_data:
                if contract.get("instrument_name") == perpetual_name:
                    continue
                    
                name = contract.get("instrument_name", "")
                if not name:
                    continue
                    
                expiration = instruments.get(name, 0)
                mark_price = contract.get("mark_price", 0)
                estimated_delivery = contract.get("estimated_delivery_price", 0)
                
                futures_list.append({
                    "name": name,
                    "expiration": expiration,
                    "apr": self.calculate_apr(mark_price, index_price, expiration),
                    "instrument": name,
                    "bid": contract.get("bid_price", 0),
                    "mark": mark_price,
                    "ask": contract.get("ask_price", 0),
                    "percent_premium": ((mark_price - estimated_delivery) / estimated_delivery) * 100 if estimated_delivery else 0,
                    "dollar_premium": mark_price - estimated_delivery,
                    "settlement": self.format_time(expiration),
                    "funding": None
                })
            
            # Sort by expiration and add to contracts
            futures_list.sort(key=lambda x: x["expiration"])
            contracts.extend(futures_list)
            
            self.logger.warning(f"Processed {len(contracts)} {currency} contracts")
            return contracts
            
        except Exception as e:
            self.logger.error(f"{currency} processing error: {e}")
            return []
    
    def update_combined_sheet(self, all_currency_data):
        """Update single sheet with selective updates - only data cells, not headers"""
        if not all_currency_data:
            return False
        
        try:
            # First time setup: check if headers exist, if not create the structure
            try:
                current_values = self.worksheet.get_all_values()
                needs_structure = len(current_values) < 5 or not any("BTC CONTRACTS" in str(row) for row in current_values)
            except:
                needs_structure = True
            
            if needs_structure:
                self.logger.warning("Setting up initial sheet structure...")
                self._setup_initial_structure(all_currency_data)
                return True
            
            # Update only data rows, keeping headers intact
            self._update_data_only(all_currency_data)
            return True
            
        except Exception as e:
            self.logger.error(f"Combined sheet update failed: {e}")
            return False
    
    def _setup_initial_structure(self, all_currency_data):
        """Set up the initial structure with headers (only run once)"""
        headers = ["APR", "Instrument", "Bid", "Mark", "Ask", "%Premium", "$Premium", "Settlement", "Funding/8h"]
        all_rows = []
        
        for currency in CONFIG["CURRENCIES"]:
            contracts = all_currency_data.get(currency, [])
            if not contracts:
                continue
            
            # Add currency header section
            all_rows.append([f"=== {currency} CONTRACTS ===", "", "", "", "", "", "", "", ""])
            all_rows.append(headers)
            
            # Add placeholder rows for data (will be updated in _update_data_only)
            for _ in contracts:
                all_rows.append(["", "", "", "", "", "", "", "", ""])
            
            # Add empty row separator
            all_rows.append(["", "", "", "", "", "", "", "", ""])
        
        # Set up the structure once
        self.worksheet.clear()
        self.worksheet.update(values=all_rows, range_name='A1')
    
    def _update_data_only(self, all_currency_data):
        """Update only the data rows, keeping headers and structure intact"""
        update_requests = []
        current_row = 1
        
        for currency in CONFIG["CURRENCIES"]:
            contracts = all_currency_data.get(currency, [])
            if not contracts:
                continue
            
            # Skip currency header row
            current_row += 1
            # Skip column headers row  
            current_row += 1
            
            # Update data rows for this currency
            for c in contracts:
                data_row = [
                    c["apr"]/100 if c["apr"] != "-" else 0,  # Convert APR to decimal for % formatting
                    c["instrument"],  # Text
                    c["bid"] if c["bid"] else 0,  # Raw bid number
                    c["mark"] if c["mark"] else 0,  # Raw mark number
                    c["ask"] if c["ask"] else 0,  # Raw ask number
                    c["percent_premium"]/100 if c["percent_premium"] else 0,  # Convert to decimal for % formatting
                    c["dollar_premium"] if c["dollar_premium"] else 0,  # Raw dollar amount
                    c["settlement"],  # Text (time format)
                    c["funding"] if c["funding"] is not None else 0  # Already in decimal form
                ]
                
                # Add this row to batch update
                range_name = f"A{current_row}:I{current_row}"
                update_requests.append({
                    'range': range_name,
                    'values': [data_row]
                })
                
                current_row += 1
            
            # Skip separator row
            current_row += 1
        
        # Batch update all data rows at once
        if update_requests:
            self.worksheet.batch_update(update_requests)
            self.logger.warning(f"Updated {len(update_requests)} data rows without refreshing headers")
    
    def fetch_and_update_all(self):
        """Fetch and update data for all currencies in single sheet"""
        all_currency_data = {}
        
        for currency in CONFIG["CURRENCIES"]:
            try:
                self.logger.warning(f"Processing {currency}...")
                contracts = self.fetch_currency_data(currency)
                
                if contracts:
                    all_currency_data[currency] = contracts
                    self.logger.warning(f"{currency}: {len(contracts)} contracts processed")
                else:
                    self.logger.error(f"{currency}: No contracts processed")
                    
            except Exception as e:
                self.logger.error(f"{currency} processing failed: {e}")
        
        # Update the combined sheet
        if all_currency_data:
            success = self.update_combined_sheet(all_currency_data)
            if success:
                total_contracts = sum(len(contracts) for contracts in all_currency_data.values())
                self.logger.warning(f"Combined sheet updated: {total_contracts} total contracts")
                return True
            else:
                self.logger.error("Combined sheet update failed")
                return False
        else:
            self.logger.error("No data to update")
            return False
    
    def run(self):
        """Main run loop"""
        self.logger.warning(f"Starting multi-currency updates every {CONFIG['UPDATE_INTERVAL']}s")
        self.logger.warning(f"Tracking currencies: {CONFIG['CURRENCIES']} in single sheet")
        
        while True:
            try:
                success = self.fetch_and_update_all()
                
                if success:
                    self.logger.warning("Update successful")
                else:
                    self.logger.error("Update failed")
                    
                time.sleep(CONFIG["UPDATE_INTERVAL"])
                
            except KeyboardInterrupt:
                self.logger.warning("Shutdown")
                break
            except Exception as e:
                self.logger.error(f"Loop error: {e}")
                time.sleep(30)

if __name__ == "__main__":
    try:
        DeribitMultiTracker().run()
    except Exception as e:
        logging.error(f"Startup failed: {e}")
        sys.exit(1)