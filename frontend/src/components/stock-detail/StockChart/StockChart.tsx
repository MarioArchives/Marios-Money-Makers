import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { StockChartProps } from "./StockChart.props";
import { useStockHistoryQuery } from "../../../api/queries";
import {
  DEFAULT_HISTORY_RANGE,
  type HistoryPoint,
  type HistoryRange,
} from "../../../api/types";
import { useFxRate } from "../../../providers/FxRateProvider/FxRateProvider";
import {
  DISPLAY_CURRENCY,
  convertToGbp,
  formatCurrency,
} from "../../../utils/currency";
import {
  buildIntradaySeries,
  isGapKey,
  MARKET_CLOSED_LABEL,
} from "../../../utils/intradaySeries";
import "./StockChart.css";

function mergePoints(
  prev: HistoryPoint[],
  incoming: HistoryPoint[],
): HistoryPoint[] {
  const seen = new Set(prev.map((point) => point.t));
  const appended = incoming.filter((point) => !seen.has(point.t));
  if (appended.length === 0) {
    return prev;
  }
  return [...prev, ...appended];
}

/** The accumulated series and which `${ticker}:${range}` it belongs to. */
interface Series {
  id: string;
  points: HistoryPoint[];
}

/** Card title per range — what the series on screen actually covers. */
export const RANGE_TITLES: Record<HistoryRange, string> = {
  "1d": "Price today",
  "30d": "Price, last 30 days",
  all: "Price, all time",
};

/**
 * ISO timestamp (categorical axes) or ms-since-epoch (the 1d proportional
 * time axis) -> axis label. Intraday reads as a trading clock ("14:35");
 * 30 days reads as dates ("19 Aug"); all-time reads as months ("Aug 2026")
 * — the span keeps growing, so month+year stays legible at any length.
 */
export function makeTimeFormatter(
  range: HistoryRange,
): (value: string | number) => string {
  return (value: string | number): string => {
    // A placeholder column of the intraday market-closed block: no tick.
    if (typeof value === "string" && isGapKey(value)) {
      return "";
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value as string;
    }
    if (range === "1d") {
      return parsed.toLocaleTimeString("en-US", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
    if (range === "30d") {
      return parsed.toLocaleDateString("en-US", {
        day: "numeric",
        month: "short",
      });
    }
    return parsed.toLocaleDateString("en-US", {
      month: "short",
      year: "numeric",
    });
  };
}

/**
 * Live price chart for the stock detail page. Polls only
 * `useStockHistoryQuery` (never the detail query). Newly-polled points are
 * appended and deduped by timestamp (`t`) into local `HistoryPoint[]`
 * state rather than replacing the array wholesale, so the underlying
 * recharts `LineChart` gets a stable, growing dataset instead of a fresh
 * array reference on every poll — avoiding an unnecessary remount/redraw.
 * The merge happens *during render* (React's "adjust state when a prop
 * changes" pattern: set state, React re-runs this component before
 * committing) rather than in an effect, so a poll costs one commit of the
 * chart tree instead of render → effect → setState → second render.
 * Renders `<Line isAnimationActive={false} .../>` so appended points don't
 * replay an entry animation on every 20s poll.
 *
 * Switching `ticker` or `range` is the one case where the accumulated
 * series is replaced wholesale — it is a different series.
 *
 * The plotted values are converted to GBP at the shared `FxRateProvider`
 * rate (memoised per points/rate pair); the accumulated series itself
 * stays in the API's native currency so a rate refresh never disturbs the
 * dedupe-by-timestamp state. Without a rate the native figures plot.
 *
 * When the history is stale/errored (or the query itself is failing), the
 * accumulated series stays exactly where it is and the card greys out —
 * the line is never cleared and never replaced by an error panel.
 *
 * Intraday (`range === '1d'`) keeps the categorical axis (every bar the
 * same width, so trading hours keep all the space) but marks each
 * market-closed period as a fixed-width block: `buildIntradaySeries`
 * inserts null-`close` placeholder columns into the plotted data where a
 * real hole in the bars falls in a closed period, a `ReferenceArea` greys
 * those columns out with the "no data" copy (drawn vertically — the block
 * is narrow), and `connectNulls={false}` breaks the line across them. The
 * accumulated series itself never holds placeholders. 30d and all-time are
 * unchanged: the raw points, no blocks.
 */
export function StockChart({
  ticker,
  range = DEFAULT_HISTORY_RANGE,
}: StockChartProps): JSX.Element {
  const query = useStockHistoryQuery(ticker, range);
  const seriesId = `${ticker}:${range}`;
  const [series, setSeries] = useState<Series>(() => ({
    id: seriesId,
    points: query.data?.points ?? [],
  }));
  const isDegraded =
    Boolean(query.data?.is_stale) ||
    Boolean(query.data?.error) ||
    Boolean(query.isError);

  // Accumulate in render: a different ticker/range replaces the series, a
  // poll on the same series appends the new points (no-op, no state write,
  // when nothing new arrived).
  let points = series.points;
  if (series.id !== seriesId) {
    points = query.data?.points ?? [];
    setSeries({ id: seriesId, points });
  } else if (query.data?.points) {
    const merged = mergePoints(series.points, query.data.points);
    if (merged !== series.points) {
      points = merged;
      setSeries({ id: seriesId, points: merged });
    }
  }

  const formatTime = makeTimeFormatter(range);
  const { rate } = useFxRate();
  // History points carry no currency field; the API's stocks are USD.
  const nativeCurrency = "USD";
  const displayCurrency = rate ? DISPLAY_CURRENCY : nativeCurrency;
  const { plotted, blocks } = useMemo(() => {
    if (range === "1d") {
      const { data, blocks: closedBlocks } = buildIntradaySeries(points);
      return {
        plotted: data.map((point) => ({
          ...point,
          close:
            point.close === null
              ? null
              : (convertToGbp(point.close, nativeCurrency, rate) ?? point.close),
        })),
        blocks: closedBlocks,
      };
    }
    return {
      plotted: points.map((point) => ({
        ...point,
        close: convertToGbp(point.close, nativeCurrency, rate) ?? point.close,
      })),
      blocks: [],
    };
  }, [points, rate, range]);
  const formatPrice = (value: number): string =>
    formatCurrency(value, displayCurrency);

  return (
    <section
      className={`stock-chart card${isDegraded ? " is-stale" : ""}`}
      data-testid="stock-chart"
      aria-label={RANGE_TITLES[range]}
    >
      <header className="stock-chart__head">
        <span className="eyebrow">{RANGE_TITLES[range]}</span>
        <span className="eyebrow stock-chart__interval">
          {query.data?.interval ?? ""} / {query.data?.range ?? ""}
        </span>
      </header>
      <div className="stock-chart__plot">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={plotted}
            margin={{ top: 4, right: 8, bottom: 0, left: -8 }}
          >
            <CartesianGrid
              stroke="var(--color-chart-grid)"
              strokeDasharray="2 4"
              vertical={false}
            />
            <XAxis
              dataKey="t"
              tickFormatter={formatTime}
              tickLine={false}
              axisLine={false}
              minTickGap={32}
              tick={{ fontSize: 11, fill: "var(--color-text-faint)" }}
            />
            <YAxis
              domain={["auto", "auto"]}
              width={64}
              tickFormatter={formatPrice}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 11, fill: "var(--color-text-faint)" }}
            />
            <Tooltip
              labelFormatter={formatTime}
              formatter={(value) => [
                formatPrice(Number(value)),
                displayCurrency,
              ]}
              contentStyle={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                borderRadius: "8px",
                fontSize: "0.8125rem",
              }}
            />
            {range === "1d" &&
              blocks.map((block) => (
                <ReferenceArea
                  key={block.x1}
                  x1={block.x1}
                  x2={block.x2}
                  fill="var(--color-chart-closed)"
                  fillOpacity={1}
                  stroke="none"
                  ifOverflow="visible"
                  label={{
                    value: MARKET_CLOSED_LABEL,
                    position: "center",
                    angle: -90,
                    fill: "var(--color-text-faint)",
                    fontSize: 11,
                  }}
                />
              ))}
            <Line
              type="monotone"
              dataKey="close"
              isAnimationActive={false}
              dot={false}
              stroke="currentColor"
              strokeWidth={2}
              connectNulls={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
