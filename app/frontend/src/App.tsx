import { useState } from 'react'
import { BrowserRouter, Routes, Route, useParams } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { ChatArea } from './components/ChatArea'
import { ToastProvider } from './components/ToastProvider'

// ── Layout wrapper used by all routes ────────────────────────────
interface AppLayoutProps {
  conversationId?: string
}

function AppLayout({ conversationId }: AppLayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="app-layout">
      {/* Mobile overlay — only rendered when sidebar is open on mobile */}
      {sidebarOpen && (
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <Sidebar
        activeConversationId={conversationId}
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="main-area">
        {/* Hamburger — visible only on mobile when sidebar is closed */}
        {!sidebarOpen && (
          <button
            className="hamburger-btn"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 18 18"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="2" y1="4.5" x2="16" y2="4.5" />
              <line x1="2" y1="9"   x2="16" y2="9"   />
              <line x1="2" y1="13.5" x2="16" y2="13.5" />
            </svg>
          </button>
        )}

        <ChatArea conversationId={conversationId} />
      </div>
    </div>
  )
}

// ── Route components ─────────────────────────────────────────────
function ConversationPage() {
  const { conversationId } = useParams<{ conversationId: string }>()
  return <AppLayout conversationId={conversationId} />
}

function LandingPage() {
  return <AppLayout conversationId={undefined} />
}

// ── Root app ─────────────────────────────────────────────────────
function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <Routes>
          <Route path="/"                    element={<LandingPage />} />
          <Route path="/c/:conversationId"   element={<ConversationPage />} />
        </Routes>
      </ToastProvider>
    </BrowserRouter>
  )
}

export default App
