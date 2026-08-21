from dataclasses import dataclass


@dataclass(frozen=True)
class TickerInfo:
    ticker: str
    name: str
    sector: str


# Fixed universe of 20 US large caps, Alpaca symbol format (note BRK.B's
# dot -- that is how Alpaca spells Berkshire's class-B shares).
TICKERS: list[TickerInfo] = [
    TickerInfo("AAPL", "Apple", "Technology"),
    TickerInfo("MSFT", "Microsoft", "Technology"),
    TickerInfo("NVDA", "NVIDIA", "Semiconductors"),
    TickerInfo("GOOGL", "Alphabet", "Technology"),
    TickerInfo("AMZN", "Amazon", "Consumer Discretionary"),
    TickerInfo("META", "Meta Platforms", "Technology"),
    TickerInfo("TSLA", "Tesla", "Automotive"),
    TickerInfo("AVGO", "Broadcom", "Semiconductors"),
    TickerInfo("BRK.B", "Berkshire Hathaway", "Financials"),
    TickerInfo("JPM", "JPMorgan Chase", "Banking"),
    TickerInfo("V", "Visa", "Financial Services"),
    TickerInfo("LLY", "Eli Lilly", "Pharmaceuticals"),
    TickerInfo("UNH", "UnitedHealth Group", "Healthcare"),
    TickerInfo("XOM", "Exxon Mobil", "Energy"),
    TickerInfo("MA", "Mastercard", "Financial Services"),
    TickerInfo("HD", "Home Depot", "Retail"),
    TickerInfo("PG", "Procter & Gamble", "Consumer Goods"),
    TickerInfo("COST", "Costco", "Retail"),
    TickerInfo("JNJ", "Johnson & Johnson", "Pharmaceuticals"),
    TickerInfo("NFLX", "Netflix", "Media"),
]

TICKER_SYMBOLS: list[str] = [t.ticker for t in TICKERS]
TICKERS_BY_SYMBOL: dict[str, TickerInfo] = {t.ticker: t for t in TICKERS}
