import { createBrowserRouter } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell/AppShell'
import { LeaderboardPage } from './pages/LeaderboardPage/LeaderboardPage'
import { StockDetailPage } from './pages/StockDetailPage/StockDetailPage'
import { DashboardPage } from './pages/DashboardPage/DashboardPage'

/** App-wide route table: `AppShell` is the layout route (nav + `<Outlet/>`) wrapping the three pages. */
export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <LeaderboardPage /> },
      { path: '/stock/:ticker', element: <StockDetailPage /> },
      { path: '/dashboard', element: <DashboardPage /> },
    ],
  },
])
