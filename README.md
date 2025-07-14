# Deribit APR Calculator

A Python script that fetches real-time data from Deribit API and calculates APR (Annual Percentage Rate) for BTC and ETH futures contracts, automatically updating Google Sheets.

## Features

- 📊 **Real-time data** from Deribit API every 10 seconds
- 🔢 **APR calculations** using Deribit's exact formula
- 💰 **Premium calculations** (% and $ premiums)
- ⏰ **Time to settlement** formatting
- 💸 **Funding rate calculations** for perpetual contracts
- 📈 **Google Sheets integration** with automatic updates
- 🚀 **AWS optimized** for production deployment

## Supported Contracts

### BTC Contracts:
- BTC-PERPETUAL (with funding rate)
- All active BTC futures contracts

### ETH Contracts:
- ETH-PERPETUAL (with funding rate)  
- All active ETH futures contracts

## Installation

1. **Clone the repository:**
```bash
git clone <your-repo-url>
cd apr_calculator
```

2. **Create virtual environment:**
```bash
python3 -m venv .
source bin/activate
```

3. **Install dependencies:**
```bash
pip install requests gspread google-auth
```

4. **Setup Google Sheets API:**
   - Create a service account in Google Cloud Console
   - Download the service account key JSON file
   - Rename it to `service-account-key.json`
   - Share your Google Sheet with the service account email

## Configuration

Update the configuration in `deribit_apr_calculator.py`:

```python
CONFIG = {
    "CREDENTIALS_FILE": "/path/to/service-account-key.json",
    "SHEET_NAME": "Your Google Sheet Name",
    "WORKSHEET_NAME": "Sheet2",
    "UPDATE_INTERVAL": 10,  # seconds
    "CURRENCIES": ["BTC", "ETH"]
}
```

## Usage

### Local Development:
```bash
python deribit_apr_calculator.py
```

### AWS Production:
```bash
nohup python deribit_apr_calculator.py > /dev/null 2>&1 &
```

## Output Format

The script updates Google Sheets with the following columns:

| Column | Description | Example |
|--------|-------------|---------|
| APR | Annual Percentage Rate | +12.34% |
| Instrument | Contract name | BTC-18JUL25 |
| Bid | Bid price | $121,845 |
| Mark | Mark price | $121,835 |
| Ask | Ask price | $121,845 |
| %Premium | Premium percentage | +0.041% |
| $Premium | Premium in dollars | $49.79 |
| Settlement | Time to settlement | 3d 18h 45m |
| Funding/8h | 8-hour funding rate | +0.012% |

## APR Calculation

Uses Deribit's exact formula:
```
APR = ((Mark Price / Index Price) - 1) * 525600 / minutes_till_expiration
```

## Funding Rate Calculation

For perpetual contracts, uses damping logic:
```
Premium Rate = ((Mark Price - Index Price) / Index Price) * 100
Damped Premium = MAX(0.025, Premium Rate) + MIN(-0.025, Premium Rate)
Funding Rate = Damped Premium / 100
```

## Google Sheets Formatting

The script sends raw numeric values. Apply these custom formats in Google Sheets:

- **APR Column:** `+0.00%;-0.00%;"-"`
- **%Premium Column:** `+0.000%;-0.000%;"-"`
- **$Premium Column:** `$#,##0.00`
- **Funding Column:** `+0.000%;-0.000%;"-"`

## Error Handling

- Automatic retry on API failures
- Graceful handling of missing data
- Error logging for troubleshooting
- 30-second recovery delay on errors

## Files

- `deribit_apr_calculator.py` - Main script
- `service-account-key.json` - Google Sheets credentials (not tracked)
- `.gitignore` - Git ignore rules
- `README.md` - This file

## Requirements

- Python 3.7+
- Google Sheets API access
- Internet connection for Deribit API

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues and questions, please open a GitHub issue.
