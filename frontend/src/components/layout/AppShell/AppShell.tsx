import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { PollCountdownBar } from "../PollCountdownBar/PollCountdownBar";
import "./AppShell.css";

const CONDENSE_SCROLL_Y = 24;

/**
 * Top-level layout route: sticky header bar (condenses on scroll via CSS
 * `is-condensed`, see ARCHITECTURE.md) plus `PollCountdownBar` and the
 * routed `<Outlet/>`. Pure plumbing/layout — no data fetching of its own.
 */
export function AppShell(): JSX.Element {
  const [condensed, setCondensed] = useState(false);

  useEffect(() => {
    const onScroll = (): void =>
      setCondensed(window.scrollY > CONDENSE_SCROLL_Y);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="app-shell">
      <header
        className={condensed ? "app-shell__bar is-condensed" : "app-shell__bar"}
      >
        <nav className="app-shell__menu" aria-label="Sections">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              isActive ? "app-shell__link active" : "app-shell__link"
            }
          >
            Leaderboard
          </NavLink>
          <NavLink
            to="/dashboard"
            className={({ isActive }) =>
              isActive ? "app-shell__link active" : "app-shell__link"
            }
          >
            Dashboard
          </NavLink>
        </nav>
        <span className="app-shell__title" data-testid="app-title">
          Mario's Money Makers
        </span>
        <span
          className="app-shell__logo"
          data-testid="app-logo"
          aria-hidden="true"
        >
          M<sup className="app-shell__logo-sup">3</sup>
        </span>
        <PollCountdownBar />
      </header>
      <main className="app-shell__content">
        <Outlet />
      </main>
    </div>
  );
}
