import type { PollCountdownBarProps } from './PollCountdownBar.props'
import { usePollCountdown } from '../../../hooks/usePollCountdown'
import './PollCountdownBar.css'

/**
 * Header's bottom border doubling as a poll countdown (drains, pulses while
 * fetching, snaps full on the shared tick — see `usePollCountdown`). Colour
 * is the header's `--countdown-color`; static full border before first poll.
 */
export function PollCountdownBar(_props: PollCountdownBarProps): JSX.Element {
  void _props
  const { remaining, secondsLeft, isFetching } = usePollCountdown()
  const percent = Math.round(remaining * 100)

  return (
    <div
      className={`poll-countdown${isFetching ? ' is-fetching' : ''}`}
      data-testid="poll-countdown"
      role="progressbar"
      aria-label="Time until next data refresh"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={isFetching ? 'Refreshing' : `${secondsLeft} s until next refresh`}
    >
      <span className="poll-countdown__fill" style={{ transform: `scaleX(${remaining})` }} />
    </div>
  )
}
