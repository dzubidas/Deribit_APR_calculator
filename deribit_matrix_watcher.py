import requests
import time
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone
import logging
import sys
import os

# Lightweight configuration
CONFIG = {
    "CREDENTIALS_FILE": os.path.join(os.path.dirname(__file__), "service-account-key.json"),
    "SHEET_ID": "1jTioi772C3hgU1KrXZAnW5JBhiEgtdJvae2qM_llmXg",
    "WORKSHEET_ID": 1711352348,
    "UPDATE_INTERVAL": 10,
    "CURRENCIES": ["BTC", "ETH"]
}

class DeribitSpreadsTracker:
    def __init__(self):
        self.base_url = "https://www.deribit.com/api/v2/public"
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'DeribitSpreadsTracker/2.0'})
        
        # Simple logging
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Setup Google Sheets
        self.setup_sheets()
        
    def setup_sheets(self):
        """Initialize Google Sheets connection using IDs"""
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_file(CONFIG["CREDENTIALS_FILE"], scopes=scope)
            self.gc = gspread.authorize(creds)
            
            # Open sheet by ID
            self.sheet = self.gc.open_by_key(CONFIG["SHEET_ID"])
            
            # Get worksheet by actual ID (not index)
            worksheets = self.sheet.worksheets()
            self.worksheet = None
            
            for ws in worksheets:
                if ws.id == CONFIG["WORKSHEET_ID"]:
                    self.worksheet = ws
                    break
            
            if not self.worksheet:
                raise Exception(f"Worksheet with ID {CONFIG['WORKSHEET_ID']} not found")
            
            self.logger.warning("Connected to Google Sheets")
        except Exception as e:
            self.logger.error(f"Sheets connection failed: {e}")
            sys.exit(1)
    
    def api_get(self, endpoint, params=None):
        """Simple API request"""
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", params=params, timeout=10)
            response.raise_for_status()
            return response.json().get("result", [])
        except:
            return []
    
    def get_orderbook(self, instrument_id):
        """Get mark price by instrument ID"""
        try:
            response = self.session.get(
                f"{self.base_url}/get_order_book_by_instrument_id",
                params={"instrument_id": instrument_id, "depth": 1},
                timeout=10
            )
            return response.json().get("result", {}).get('mark_price')
        except:
            return None
    
    def get_perpetual_price(self, currency):
        """Get perpetual swap price"""
        try:
            response = self.session.get(
                f"{self.base_url}/get_order_book",
                params={"instrument_name": f"{currency}-PERPETUAL", "depth": 1},
                timeout=10
            )
            return response.json().get("result", {}).get('mark_price')
        except:
            return None
    
    def get_contracts(self, currency):
        """Get sorted futures contracts"""
        instruments = self.api_get("get_instruments", {"currency": currency, "expired": "false"})
        
        contracts = []
        for inst in instruments:
            if inst.get("kind") == "future":
                contracts.append({
                    "id": inst.get("instrument_id"),
                    "name": inst.get("instrument_name"),
                    "exp": inst.get("expiration_timestamp", 0)
                })
        
        # Sort: perpetual first, then by expiration
        perpetual = [c for c in contracts if c["name"].endswith("-PERPETUAL")]
        futures = sorted([c for c in contracts if not c["name"].endswith("-PERPETUAL")], key=lambda x: x["exp"])
        
        return perpetual + futures
    
    def find_spread_id(self, currency, contract1, contract2, instruments):
        """Find spread instrument ID"""
        def extract_date(name):
            return "PERP" if name.endswith("-PERPETUAL") else name.split("-", 1)[1]
        
        date1, date2 = extract_date(contract1), extract_date(contract2)
        
        for pattern in [f"{currency}-FS-{date1}_{date2}", f"{currency}-FS-{date2}_{date1}"]:
            if pattern in instruments:
                return instruments[pattern].get("instrument_id")
        return None
    
    def create_matrix(self, currency):
        """Create spreads matrix with perpetual price"""
        # Get all instruments
        all_instruments = self.api_get("get_instruments", {"currency": currency, "expired": "false"})
        instruments_dict = {inst.get("instrument_name"): inst for inst in all_instruments}
        
        # Get contracts
        contracts = self.get_contracts(currency)
        if len(contracts) < 2:
            return [], None
        
        # Get perpetual price
        perp_price = self.get_perpetual_price(currency)
        perp_price_display = f"{perp_price:.2f}" if perp_price else "-"
        
        matrix = []
        
        # Header row: perpetual price in A1, then contract names
        header = [perp_price_display] + [c["name"] for c in contracts]
        matrix.append(header)
        
        # Data rows
        for row_idx, row_contract in enumerate(contracts):
            row = [row_contract["name"]]
            
            for col_idx in range(len(contracts)):
                if row_idx == col_idx:  # Diagonal
                    row.append("-")
                elif row_idx < col_idx:  # Upper triangle
                    spread_id = self.find_spread_id(currency, row_contract["name"], contracts[col_idx]["name"], instruments_dict)
                    if spread_id:
                        mark_price = self.get_orderbook(spread_id)
                        row.append(f"{mark_price:.1f}" if mark_price else "")
                    else:
                        row.append("")
                else:  # Lower triangle
                    row.append("-")
            
            matrix.append(row)
        
        return matrix, perp_price
    
    def create_percentage_matrix(self, spread_matrix, perp_price):
        """Create percentage matrix (spread / perpetual price * 100)"""
        if not perp_price or perp_price == 0:
            return []
        
        percentage_matrix = []
        
        # Copy header row
        percentage_matrix.append(spread_matrix[0].copy())
        
        # Convert data rows to percentages
        for row in spread_matrix[1:]:  # Skip header
            new_row = [row[0]]  # Keep contract name
            
            for cell_value in row[1:]:
                if cell_value and cell_value != "-":
                    try:
                        spread_value = float(cell_value)
                        percentage = (spread_value / perp_price) * 100
                        new_row.append(f"{percentage:.4f}%")
                    except:
                        new_row.append("")
                else:
                    new_row.append(cell_value)
            
            percentage_matrix.append(new_row)
        
        return percentage_matrix
    
    def update_sheet(self, all_spread_data, all_percentage_data):
        """Update single sheet with both matrices"""
        try:
            current_data = self.worksheet.get_all_values()
            
            # Build new data
            new_data = []
            timestamp = f"Last Updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            new_data.append([timestamp])
            new_data.append([])
            new_data.extend(all_spread_data)
            new_data.append([])
            new_data.append([])
            new_data.extend(all_percentage_data)
            
            # Find changes
            changes = []
            for row_idx, new_row in enumerate(new_data):
                current_row = current_data[row_idx] if row_idx < len(current_data) else []
                
                for col_idx, new_value in enumerate(new_row):
                    current_value = current_row[col_idx] if col_idx < len(current_row) else ""
                    if str(new_value) != str(current_value):
                        cell_address = gspread.utils.rowcol_to_a1(row_idx + 1, col_idx + 1)
                        changes.append({'range': cell_address, 'values': [[new_value]]})
            
            # Apply changes
            if changes:
                self.worksheet.batch_update(changes)
                self.logger.warning(f"Updated {len(changes)} cells")
            else:
                self.logger.warning("No changes detected")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Update failed: {e}")
            return False
    
    def run_once(self):
        """Single update cycle"""
        all_matrices = {}
        
        for currency in CONFIG["CURRENCIES"]:
            try:
                matrix, perp_price = self.create_matrix(currency)
                if matrix:
                    all_matrices[currency] = {'spread_matrix': matrix, 'perp_price': perp_price}
                    self.logger.warning(f"{currency}: Matrix created (PERP: {perp_price:.3f})")
            except Exception as e:
                self.logger.error(f"{currency} failed: {e}")
        
        if all_matrices:
            # Build spread data
            spread_data = []
            for currency in CONFIG["CURRENCIES"]:
                if currency in all_matrices:
                    spread_data.extend(all_matrices[currency]['spread_matrix'])
                    spread_data.append([])
                    spread_data.append([])
            
            # Build percentage data
            percentage_data = []
            for currency in CONFIG["CURRENCIES"]:
                if currency in all_matrices:
                    matrix_data = all_matrices[currency]
                    percentage_matrix = self.create_percentage_matrix(
                        matrix_data['spread_matrix'], 
                        matrix_data['perp_price']
                    )
                    if percentage_matrix:
                        percentage_data.extend(percentage_matrix)
                        percentage_data.append([])
                        percentage_data.append([])
            
            return self.update_sheet(spread_data, percentage_data)
        
        return False
    
    def run(self):
        """Main loop"""
        self.logger.warning(f"Starting tracker every {CONFIG['UPDATE_INTERVAL']}s")
        
        while True:
            try:
                success = self.run_once()
                if success:
                    self.logger.warning("Update completed")
                else:
                    self.logger.error("Update failed")
                
                time.sleep(CONFIG['UPDATE_INTERVAL'])
                
            except KeyboardInterrupt:
                self.logger.warning("Shutdown")
                break
            except Exception as e:
                self.logger.error(f"Loop error: {e}")
                time.sleep(60)

if __name__ == "__main__":
    try:
        DeribitSpreadsTracker().run()
    except Exception as e:
        logging.error(f"Startup failed: {e}")
        sys.exit(1)