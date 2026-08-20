from dataclasses import dataclass


@dataclass(frozen=True)
class TickerInfo:
    ticker: str
    name: str
    sector: str


TICKERS: list[TickerInfo] = [
    TickerInfo("AZN.L", "AstraZeneca", "Pharmaceuticals"),
    TickerInfo("GSK.L", "GSK", "Pharmaceuticals"),
    TickerInfo("ULVR.L", "Unilever", "Consumer Goods"),
    TickerInfo("RKT.L", "Reckitt Benckiser", "Consumer Goods"),
    TickerInfo("DGE.L", "Diageo", "Beverages"),
    TickerInfo("BATS.L", "British American Tobacco", "Tobacco"),
    TickerInfo("SHEL.L", "Shell", "Energy"),
    TickerInfo("BP.L", "BP", "Energy"),
    TickerInfo("RIO.L", "Rio Tinto", "Mining"),
    TickerInfo("GLEN.L", "Glencore", "Mining"),
    TickerInfo("HSBA.L", "HSBC", "Banking"),
    TickerInfo("BARC.L", "Barclays", "Banking"),
    TickerInfo("LLOY.L", "Lloyds Banking Group", "Banking"),
    TickerInfo("PRU.L", "Prudential", "Insurance"),
    TickerInfo("AV.L", "Aviva", "Insurance"),
    TickerInfo("VOD.L", "Vodafone Group", "Telecom"),
    TickerInfo("BT-A.L", "BT Group", "Telecom"),
    TickerInfo("TSCO.L", "Tesco", "Retail"),
    TickerInfo("NG.L", "National Grid", "Utilities"),
    TickerInfo("RR.L", "Rolls-Royce Holdings", "Aerospace"),
]

TICKER_SYMBOLS: list[str] = [t.ticker for t in TICKERS]
TICKERS_BY_SYMBOL: dict[str, TickerInfo] = {t.ticker: t for t in TICKERS}
