import time
import os
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from datetime import datetime
import pytz

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

ALPACA_API_KEY     = os.environ.get("ALPACA_API_KEY",     "AKOKP4ORECOVLA4TDN46EHW3T7")
ALPACA_SECRET_KEY  = os.environ.get("ALPACA_SECRET_KEY",  "2o3j8oiwBcQ6hznhdWV6-JMJUa8R6gi7YghBtx8MR8r")
NEWS_API_KEY       = os.environ.get("NEWS_API_KEY",       "1fddfd387861474fb1e9e063b89645d9")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS",      "justin.stocks.api@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "vkhovfdztwkjuxue")
PHONE_NUMBER       = os.environ.get("PHONE_NUMBER",       "8167210604")
VTEXT_ADDRESS      = PHONE_NUMBER + "@vtext.com"

STARTING_BALANCE   = 50.0
MILESTONE_EVERY    = 100.0
WITHDRAW_AT        = 1000.0
STOP_LOSS_PCT      = 0.03
TAKE_PROFIT_PCT    = 0.08
MIN_SIGNAL_SCORE   = 3
MAX_POSITIONS      = 4
TRADE_SIZE_PCT     = 0.20
MAX_TRADE_USD      = 12.0
COOLDOWN_CYCLES    = 2
RED_DAY_THRESHOLD  = -0.01

TRUSTED_SOURCES = {
    'reuters.com', 'bloomberg.com', 'wsj.com', 'apnews.com',
    'cnbc.com', 'ft.com', 'marketwatch.com', 'finance.yahoo.com',
    'businessinsider.com', 'forbes.com', 'barrons.com'
}

ET = pytz.timezone('America/New_York')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=False)
data_client    = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

POSITIVE_WORDS = {
    "surge":2,"soar":2,"record":2,"beat":2,"blowout":3,
    "profit":1,"growth":1,"gain":1,"rally":2,"boost":1,
    "bullish":2,"upgrade":2,"buy":1,"strong":1,"rise":1,
    "breakthrough":2,"expansion":1,"partnership":1,"deal":1,
    "outperform":2,"exceed":2,"innovation":1,"demand":1,
}
NEGATIVE_WORDS = {
    "crash":3,"fall":1,"loss":2,"miss":2,"drop":1,
    "recession":3,"bearish":2,"downgrade":2,"sell":1,
    "weak":1,"decline":1,"layoff":2,"bankrupt":3,
    "risk":1,"concern":1,"warning":2,"lawsuit":2,
    "investigation":2,"recall":2,"tariff":1,
    "fine":1,"penalty":2,"hack":2,"breach":2,
}
TICKER_KEYWORDS = {
    "apple":"AAPL","iphone":"AAPL",
    "microsoft":"MSFT","azure":"MSFT",
    "amazon":"AMZN","aws":"AMZN",
    "google":"GOOGL","alphabet":"GOOGL",
    "nvidia":"NVDA","gpu":"NVDA",
    "meta":"META","facebook":"META","instagram":"META",
    "tesla":"TSLA","elon":"TSLA",
    "netflix":"NFLX","salesforce":"CRM",
    "jpmorgan":"JPM","bank":"JPM","goldman":"GS",
    "fed":"SPY","federal reserve":"SPY","interest rate":"SPY",
    "oil":"XOM","exxon":"XOM","energy":"XOM","opec":"XOM",
    "pfizer":"PFE","vaccine":"PFE","drug":"JNJ","fda":"JNJ",
    "economy":"SPY","market":"SPY","nasdaq":"QQQ","tech":"QQQ",
    "recession":"SPY","tariff":"SPY","trade war":"SPY",
}

def get_et_time():
    return datetime.now(ET)

def is_market_open():
    now = get_et_time()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 570 <= mins < 960

def is_premarket():
    now = get_et_time()
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 540 <= mins < 570

def seconds_until_open():
    from datetime import timedelta
    now = get_et_time()
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now >= market_open:
        market_open += timedelta(days=1)
        while market_open.weekday() >= 5:
            market_open += timedelta(days=1)
    return max(0, int((market_open - now).total_seconds()))

def is_red_day():
    try:
        from datetime import date, timedelta
        req = StockBarsRequest(
            symbol_or_symbols="SPY",
            timeframe=TimeFrame.Day,
            start=date.today() - timedelta(days=2),
            end=date.today()
        )
        bars = data_client.get_stock_bars(req)["SPY"]
        if len(bars) >= 2:
            change = (bars[-1].close - bars[-2].close) / bars[-2].close
            log.info("SPY change: %.2f%%" % (change * 100))
            return change < RED_DAY_THRESHOLD
    except Exception as e:
        log.warning("Red day check failed: %s" % e)
    return False

def send_sms(message):
    try:
        msg = MIMEText(message)
        msg["From"]    = GMAIL_ADDRESS
        msg["To"]      = VTEXT_ADDRESS
        msg["Subject"] = ""
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, VTEXT_ADDRESS, msg.as_string())
        log.info("SMS sent: %s" % message)
    except Exception as e:
        log.error("SMS failed: %s" % e)

def is_trusted_source(url):
    if not url:
        return False
    return any(domain in url for domain in TRUSTED_SOURCES)

def get_news_signals():
    url = ("https://newsapi.org/v2/top-headlines"
           "?category=business&language=en&pageSize=30&apiKey=%s" % NEWS_API_KEY)
    try:
        resp     = requests.get(url, timeout=10)
        articles = resp.json().get("articles", [])
    except Exception as e:
        log.error("News fetch failed: %s" % e)
        return []
    ticker_scores  = {}
    ticker_reasons = {}
    for article in articles:
        if not is_trusted_source(article.get("url", "")):
            continue
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
            if word in text:
                score += weight
        for word, weight in NEGATIVE_WORDS.items():
            if word in text:
                score -= weight
        if score == 0:
            continue
        for sym in matched:
            contribution = min(abs(score), 3) * (1 if score > 0 else -1)
            ticker_scores[sym] = ticker_scores.get(sym, 0) + contribution
            if sym not in ticker_reasons:
                ticker_reasons[sym] = article.get("title", "")
    signals = []
    for sym, total_score in ticker_scores.items():
        if abs(total_score) >= MIN_SIGNAL_SCORE:
            sentiment = "BUY" if total_score > 0 else "SELL"
            signals.append((sym, sentiment, total_score, ticker_reasons.get(sym, "")))
            log.info("Signal: %s %s score=%+d" % (sentiment, sym, total_score))
    signals.sort(key=lambda x: abs(x[2]), reverse=True)
    return signals

def get_account():
    return trading_client.get_account()

def get_portfolio_value():
    return float(get_account().​​​​​​​​​​​​​​​​
