import type { ChangeIndicatorProps } from "./ChangeIndicator.props";
import "./ChangeIndicator.css";
import { useEffect, useState } from "react";

/** Direction is carried by colour and a glyph, so it survives stale rows and colour-blind readers. */
export function ChangeIndicator({
  changePercent,
}: ChangeIndicatorProps): JSX.Element {
  const [flash, setFlash] = useState(true);
  let stateClass: "up" | "down" | "neutral" = "neutral";
  let arrow = "—";

  useEffect(() => {
    setFlash(true);
    const timer = setTimeout(() => {
      setFlash(false);
    }, 1000);
    return () => clearTimeout(timer);
  }, [changePercent]);

  if (changePercent !== null && changePercent > 0) {
    stateClass = "up";
    arrow = "▲";
  } else if (changePercent !== null && changePercent < 0) {
    stateClass = "down";
    arrow = "▼";
  }

  const label = changePercent !== null ? `${changePercent.toFixed(2)}%` : "—";

  return (
    <span
      className={`change-indicator ${flash ? "flashing" : ""} ${stateClass}`}
      data-testid="change-indicator"
    >
      <span className="change-indicator__arrow" aria-hidden="true">
        {arrow}
      </span>
      <span className="change-indicator__value numeral">{label}</span>
    </span>
  );
}
