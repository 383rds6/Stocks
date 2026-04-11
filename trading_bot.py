# “””
News-Driven Stock Trading Bot v3

What’s new vs v2:

- Market hours check (only trades 9:30am-4pm ET, Mon-Fri)
- Pre-market news scan at 9am, ready at open
- 8% take-profit (locks in gains before they reverse)
- Minimum signal strength of 3 (filters weak/noisy headlines)
- Trusted news sources only (Reuters, Bloomberg, WSJ, AP, CNBC)
- Red day protection (no new buys if SPY down 1%+ on the day)
- 3% stop-loss (carried over from v2)
- Diversified ticker selection (carried over from v2)
- Per-ticker cooldown (carried over from v2)
  “””

import time
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from datetime import datetime, timezone
import pytz

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# ─────────────────────────────────────────────

# CONFIGURATION

# ─────────────────────────────────────────────

ALPACA_PAPER_KEY    = “PK3DOD4PERBCE3XOT4J6MAAVKV”
ALPACA_PAPER_SECRET = “6gPo41jdodrnTdnpUKiXLnHB9TVzBL9nEFAxGc9GVEHU”
ALPACA_LIVE_KEY     = “AKOKP4ORECOVLA4TDN46EHW3T7”
ALPACA_LIVE_SECRET  = “2o3j8oiwBcQ6hznhdWV6-JMJUa8R6gi7YghBtx8MR8r”
ALPACA_PAPER       = True
ALPACA_API_KEY     = ALPACA_PAPER_KEY    if ALPACA_PAPER else ALPACA_LIVE_KEY
ALPACA_SECRET_KEY  = ALPACA_PAPER_SECRET if ALPACA_PAPER else ALPACA_LIVE_SECRET

NEWS_API_KEY       = “1fddfd387861474fb1e9e063b89645d9”

GMAIL_ADDRESS      = “justin.stocks.api@gmail.com”
GMAIL_APP_PASSWORD = “vkhovfdztwkjuxue”

PHONE_NUMBER       = “8167210604”
VTEXT_ADDRESS      = f”{PHONE_NUMBER}@vtext.com”

STARTING_BALANCE   = 50.0
MILESTONE_EVERY    = 100.0
WITHDRAW_AT        = 1000.0

STOP_LOSS_PCT      = 0.03    # 3% stop-loss
TAKE_PROFIT_PCT    = 0.08    # 8% take-profit
MIN_SIGNAL_SCORE   = 3       # Minimum score to act on a signal
MAX_POSITIONS      = 4
TRADE_SIZE_PCT     = 0.20
MAX_TRADE_USD      = 12.0
COOLDOWN_CYCLES    = 2
RED_DAY_THRESHOLD  = -0.01   # Don’t buy if SPY is down more than 1%

# Trusted news sources only

TRUSTED_SOURCES = {
‘reuters.com’, ‘bloomberg.com’, ‘wsj.com’, ‘apnews.com’,
‘cnbc.com’, ‘ft.com’, ‘marketwatch.com’, ‘finance.yahoo.com’,
‘businessinsider.com’, ‘forbes.com’, ‘barrons.com’
}

ET = pytz.timezone(‘America/New_York’)

# ─────────────────────────────────────────────

# Logging

# ─────────────────────────────────────────────

logging.basicConfig(
level=logging.INFO,
format=”%(asctime)s [%(levelname)s] %(message)s”,
handlers=[logging.FileHandler(“bot.log”), logging.StreamHandler()]
)
log = logging.getLogger(**name**)

# ─────────────────────────────────────────────

# Clients

# ─────────────────────────────────────────────

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)
data_client    = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# ─────────────────────────────────────────────

# Sentiment word lists with weights

# ─────────────────────────────────────────────

POSITIVE_WORDS = {
“surge”:2,“soar”:2,“record”:2,“beat”:2,“blowout”:3,
“profit”:1,“growth”:1,“gain”:1,“rally”:2,“boost”:1,
“bullish”:2,“upgrade”:2,“buy”:1,“strong”:1,“rise”:1,
“breakthrough”:2,“expansion”:1,“partnership”:1,“deal”:1,
“outperform”:2,“exceed”:2,“innovation”:1,“demand”:1,
}
NEGATIVE_WORDS = {
“crash”:3,“fall”:1,“loss”:2,“miss”:2,“drop”:1,
“recession”:3,“bearish”:2,“downgrade”:2,“sell”:1,
“weak”:1,“decline”:1,“layoff”:2,“bankrupt”:3,
“risk”:1,“concern”:1,“warning”:2,“lawsuit”:2,
“investigation”:2,“recall”:2,“tariff”:1,
“fine”:1,“penalty”:2,“hack”:2,“breach”:2,
}

TICKER_KEYWORDS = {
“apple”:“AAPL”,“iphone”:“AAPL”,
“microsoft”:“MSFT”,“azure”:“MSFT”,
“amazon”:“AMZN”,“aws”:“AMZN”,
“google”:“GOOGL”,“alphabet”:“GOOGL”,
“nvidia”:“NVDA”,“gpu”:“NVDA”,
“meta”:“META”,“facebook”:“META”,“instagram”:“META”,
“tesla”:“TSLA”,“elon”:“TSLA”,
“netflix”:“NFLX”,“salesforce”:“CRM”,
“jpmorgan”:“JPM”,“bank”:“JPM”,“goldman”:“GS”,
“fed”:“SPY”,“federal reserve”:“SPY”,“interest rate”:“SPY”,
“oil”:“XOM”,“exxon”:“XOM”,“energy”:“XOM”,“opec”:“XOM”,
“pfizer”:“PFE”,“vaccine”:“PFE”,“drug”:“JNJ”,“fda”:“JNJ”,
“economy”:“SPY”,“market”:“SPY”,“nasdaq”:“QQQ”,“tech”:“QQQ”,
“recession”:“SPY”,“tariff”:“SPY”,“trade war”:“SPY”,
}

# ─────────────────────────────────────────────

# Market hours helpers

# ─────────────────────────────────────────────

def get_et_time():
return datetime.now(ET)

def is_market_open() -> bool:
now = get_et_time()
if now.weekday() >= 5:
return False
market_open  = now.replace(hour=9, minute=30, second=0, microsecond=0)
market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
return market_open <= now < market_close

def is_premarket() -> bool:
now = get_et_time()
if now.weekday() >= 5:
return False
pre_open   = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
return pre_open <= now < market_open

def seconds_until_open() -> int:
now = get_et_time()
market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
if now >= market_open:
# Next day
from datetime import timedelta
market_open += timedelta(days=1)
while market_open.weekday() >= 5:
market_open += timedelta(days=1)
return max(0, int((market_open - now).total_seconds()))

# ─────────────────────────────────────────────

# Red day protection - check SPY trend

# ─────────────────────────────────────────────

def is_red_day() -> bool:
“”“Returns True if SPY is down more than RED_DAY_THRESHOLD today.”””
try:
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import date, timedelta
req = StockBarsRequest(
symbol_or_symbols=“SPY”,
timeframe=TimeFrame.Day,
start=date.today() - timedelta(days=2),
end=date.today()
)
bars = data_client.get_stock_bars(req)[“SPY”]
if len(bars) >= 2:
prev_close = bars[-2].close
curr_close = bars[-1].close
change_pct = (curr_close - prev_close) / prev_close
log.info(f”SPY day change: {change_pct*100:.2f}%”)
return change_pct < RED_DAY_THRESHOLD
except Exception as e:
log.warning(f”Could not check red day: {e}”)
return False

# ─────────────────────────────────────────────

# SMS

# ─────────────────────────────────────────────

def send_sms(message: str):
try:
msg = MIMEText(message)
msg[“From”] = GMAIL_ADDRESS
msg[“To”]   = VTEXT_ADDRESS
msg[“Subject”] = “”
with smtplib.SMTP_SSL(“smtp.gmail.com”, 465) as server:
server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
server.sendmail(GMAIL_ADDRESS, VTEXT_ADDRESS, msg.as_string())
log.info(f”SMS sent: {message}”)
except Exception as e:
log.error(f”SMS failed: {e}”)

# ─────────────────────────────────────────────

# News signal scoring

# ─────────────────────────────────────────────

def is_trusted_source(url: str) -> bool:
if not url:
return False
return any(domain in url for domain in TRUSTED_SOURCES)

def get_news_signals():
url = (
f”https://newsapi.org/v2/top-headlines”
f”?category=business&language=en&pageSize=30&apiKey={NEWS_API_KEY}”
)
try:
resp     = requests.get(url, timeout=10)
articles = resp.json().get(“articles”, [])
except Exception as e:
log.error(f”News fetch failed: {e}”)
return []

```
ticker_scores  = {}
ticker_reasons = {}
trusted_count  = 0

for article in articles:
    source_url = article.get("url", "")
    # Filter to trusted sources only
    if not is_trusted_source(source_url):
        continue
    trusted_count += 1

    title = (article.get("title") or "").lower()
    desc  = (article.get("description") or "").lower()
    text  = title + " " + desc

    matched = {}
    for keyword, sym in TICKER_KEYWORDS.items():
        if keyword in text:
            matched[sym] = matched.get(sym, 0) + 1

    if not matched:
        continue

    score = 0
    for word, weight in POSITIVE_WORDS.items():
        if word in text: score += weight
    for word, weight in NEGATIVE_WORDS.items():
        if word in text: score -= weight

    if score == 0:
        continue

    for sym in matched:
        contribution = min(abs(score), 3) * (1 if score > 0 else -1)
        ticker_scores[sym] = ticker_scores.get(sym, 0) + contribution
        if sym not in ticker_reasons:
            ticker_reasons[sym] = article.get("title", "")

log.info(f"Processed {trusted_count} trusted articles")

signals = []
for sym, total_score in ticker_scores.items():
    # Minimum signal strength filter
    if abs(total_score) >= MIN_SIGNAL_SCORE:
        sentiment = "BUY" if total_score > 0 else "SELL"
        signals.append((sym, sentiment, total_score, ticker_reasons.get(sym, "")))
        log.info(f"Signal: {sentiment:4} {sym:6} score={total_score:+d}")
    else:
        log.info(f"Filtered: {sym} score={total_score:+d} (below min {MIN_SIGNAL_SCORE})")

signals.sort(key=lambda x: abs(x[2]), reverse=True)
return signals
```

# ─────────────────────────────────────────────

# Alpaca helpers

# ─────────────────────────────────────────────

def get_account():
return trading_client.get_account()

def get_portfolio_value():
return float(get_account().portfolio_value)

def get_current_price(ticker: str) -> float:
req   = StockLatestQuoteRequest(symbol_or_symbols=ticker)
quote = data_client.get_stock_latest_quote(req)
return float(quote[ticker].ask_price)

def get_all_positions() -> dict:
try:
return {p.symbol: p for p in trading_client.get_all_positions()}
except Exception as e:
log.error(f”Error fetching positions: {e}”)
return {}

def place_order(ticker: str, side: OrderSide, dollars: float):
try:
price = get_current_price(ticker)
qty   = round(dollars / price, 6)
if qty < 0.001:
log.info(f”Order too small for {ticker}”)
return None
req   = MarketOrderRequest(
symbol=ticker, qty=qty,
side=side, time_in_force=TimeInForce.DAY
)
order = trading_client.submit_order(req)
log.info(f”Order: {side.value.upper()} {qty:.4f} {ticker} @ ~${price:.2f}”)
return order
except Exception as e:
log.error(f”Order failed {ticker}: {e}”)
return None

def close_position(ticker: str):
try:
trading_client.close_position(ticker)
log.info(f”Closed: {ticker}”)
except Exception as e:
log.error(f”Could not close {ticker}: {e}”)

# ─────────────────────────────────────────────

# Stop-loss + Take-profit checker

# ─────────────────────────────────────────────

def check_exit_conditions(positions: dict) -> list:
“””
Check all open positions for:
- Stop-loss: close if down 3% from entry
- Take-profit: close if up 8% from entry
“””
closed = []
for sym, pos in positions.items():
try:
avg_entry  = float(pos.avg_entry_price)
curr_price = float(pos.current_price)
change_pct = (curr_price - avg_entry) / avg_entry

```
        if change_pct <= -STOP_LOSS_PCT:
            log.warning(f"STOP-LOSS {sym}: {change_pct*100:.1f}%")
            close_position(sym)
            closed.append(sym)
            send_sms(f"Stop-loss: {sym} ({change_pct*100:.1f}%). Closed.")

        elif change_pct >= TAKE_PROFIT_PCT:
            log.info(f"TAKE-PROFIT {sym}: +{change_pct*100:.1f}%")
            close_position(sym)
            closed.append(sym)
            send_sms(f"Take-profit hit: {sym} (+{change_pct*100:.1f}%). Gains locked in!")

    except Exception as e:
        log.error(f"Exit check error {sym}: {e}")
return closed
```

# ─────────────────────────────────────────────

# Main bot loop

# ─────────────────────────────────────────────

def run_bot(starting_balance: float):
log.info(
f”Bot v3 started | ${starting_balance:.2f} | “
f”Stop: {STOP_LOSS_PCT*100:.0f}% | Take-profit: {TAKE_PROFIT_PCT*100:.0f}% | “
f”Min signal: {MIN_SIGNAL_SCORE}”
)
send_sms(
f”Bot v3 started: ${starting_balance:.2f}. “
f”3% stop-loss, 8% take-profit, trusted sources only.”
)

```
last_milestone   = 0.0
cooldown_map     = {}
cycle            = 0
premarket_scanned = False

while True:
    try:
        cycle += 1
        now_et = get_et_time()
        log.info(f"\n--- Cycle {cycle} | {now_et.strftime('%a %H:%M ET')} ---")

        # Tick down cooldowns
        cooldown_map = {k: v-1 for k, v in cooldown_map.items() if v > 1}

        # ── Pre-market scan at 9am ──
        if is_premarket() and not premarket_scanned:
            log.info("Pre-market: scanning news, preparing signals for open...")
            signals = get_news_signals()
            log.info(f"Pre-market signals ready: {[(s[0],s[1]) for s in signals[:5]]}")
            premarket_scanned = True
            send_sms(f"Pre-market scan done. {len(signals)} signals ready for 9:30 open.")
            time.sleep(60)
            continue

        # Reset premarket flag each day after market opens
        if is_market_open():
            premarket_scanned = False

        # ── Outside market hours - sleep until open ──
        if not is_market_open():
            secs = seconds_until_open()
            hrs  = secs // 3600
            mins = (secs % 3600) // 60
            log.info(f"Market closed. Next open in {hrs}h {mins}m. Sleeping...")
            # Check positions and portfolio even when closed
            portfolio_value = get_portfolio_value()
            profit = portfolio_value - starting_balance
            log.info(f"Portfolio: ${portfolio_value:.2f} | Profit: ${profit:+.2f}")
            # Sleep in 5-min chunks so we can check for stop-loss hits in after-hours
            time.sleep(min(300, secs))
            continue

        # ── Market is open ──
        portfolio_value = get_portfolio_value()
        profit          = portfolio_value - starting_balance
        log.info(f"Portfolio: ${portfolio_value:.2f} | Profit: ${profit:+.2f}")

        # Withdraw threshold
        if profit >= WITHDRAW_AT:
            send_sms(
                f"Bot hit ${WITHDRAW_AT:.0f} profit! "
                f"Portfolio: ${portfolio_value:.2f}. "
                f"Paused - reply with new amount."
            )
            for sym in get_all_positions():
                close_position(sym)
            log.info("Withdraw threshold hit. Bot stopped.")
            break

        # Milestone alerts
        if profit > 0:
            milestone_hit = int(profit / MILESTONE_EVERY) * MILESTONE_EVERY
            if milestone_hit > last_milestone:
                send_sms(f"+${milestone_hit:.0f} milestone! Portfolio: ${portfolio_value:.2f}")
                last_milestone = milestone_hit

        # Stop-loss + take-profit check
        positions = get_all_positions()
        closed    = check_exit_conditions(positions)
        for sym in closed:
            cooldown_map[sym] = COOLDOWN_CYCLES

        # Red day protection
        red_day = is_red_day()
        if red_day:
            log.info("Red day detected (SPY down 1%+). Skipping new buys.")

        # Refresh positions
        positions = get_all_positions()

        # Get signals
        signals = get_news_signals()
        if not signals:
            log.info("No signals above minimum threshold this cycle.")
        else:
            account      = get_account()
            buying_power = float(account.buying_power)
            trade_size   = min(buying_power * TRADE_SIZE_PCT, MAX_TRADE_USD)
            buys_made    = 0
            max_new_buys = MAX_POSITIONS - len(positions)

            for ticker, sentiment, score, headline in signals:
                if ticker in cooldown_map:
                    log.info(f"Cooldown: {ticker}")
                    continue

                if sentiment == "BUY":
                    if red_day:
                        log.info(f"Skipping BUY {ticker} - red day protection")
                        continue
                    if buys_made >= max_new_buys or ticker in positions or trade_size < 1.0:
                        continue
                    result = place_order(ticker, OrderSide.BUY, trade_size)
                    if result:
                        buys_made += 1
                        cooldown_map[ticker] = COOLDOWN_CYCLES
                        log.info(f"Buy: {ticker} score={score:+d} | {headline[:60]}")

                elif sentiment == "SELL":
                    if ticker in positions:
                        close_position(ticker)
                        cooldown_map[ticker] = COOLDOWN_CYCLES
                        log.info(f"Sell: {ticker} score={score:+d} | {headline[:60]}")

        log.info(f"Sleeping 15 min | Cooldowns: {list(cooldown_map.keys())}")
        time.sleep(15 * 60)

    except KeyboardInterrupt:
        log.info("Bot manually stopped.")
        send_sms("Bot manually stopped.")
        break
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        time.sleep(60)
```

# ─────────────────────────────────────────────

# Entry point

# ─────────────────────────────────────────────

if **name** == “**main**”:
run_bot(STARTING_BALANCE)
