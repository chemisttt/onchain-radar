import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import AppLayout from './components/layout/AppLayout'
import FeedPanel from './components/feed/FeedPanel'
import TokenPanel from './components/token/TokenPanel'
import WatchlistPanel from './components/watchlist/WatchlistPanel'
import FundingPanel from './components/funding/FundingPanel'
import FundingArb from './components/funding/FundingArb'
import AnalyzerPanel from './components/analyzer/AnalyzerPanel'
import DerivativesPanel from './components/derivatives/DerivativesPanel'
import TradingPanel from './components/trading/TradingPanel'
import DocsPanel from './components/docs/DocsPanel'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppLayout
        feed={<FeedPanel />}
        detail={<TokenPanel />}
        watchlist={<WatchlistPanel />}
        funding={<FundingPanel />}
        fundingArb={<FundingArb />}
        analyzer={<AnalyzerPanel />}
        derivatives={<DerivativesPanel />}
        trading={<TradingPanel />}
        docs={<DocsPanel />}
      />
    </QueryClientProvider>
  )
}
