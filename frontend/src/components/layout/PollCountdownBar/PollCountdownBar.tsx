import type { PollCountdownBarProps } from './PollCountdownBar.props'
import { usePollCountdown } from '../../../hooks/usePollCountdown'
import './PollCountdownBar.css'

/**
 * The header's bottom border doubling as a poll countdown: the line drains
 * from right to left over the 20 s poll interval, pulses while a fetch is
 * in flight, and snaps back to full on the shared poll tick that every
 * stock query refetches on (`usePollCountdown`). Without a query client /
 * before the first poll it is simply a full, static border. Its colour is
 * the header's `--countdown-color` (brass at rest, flipped to white once
 * the bar itself turns brass on scroll).
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
