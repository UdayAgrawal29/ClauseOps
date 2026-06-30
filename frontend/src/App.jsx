import { useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from './store/auth';
import AuthLayout from './components/AuthLayout.jsx';
import ProtectedLayout from './components/ProtectedLayout.jsx';
import Login from './pages/Login.jsx';
import Register from './pages/Register.jsx';
import Upload from './pages/Upload.jsx';
import Dashboard from './pages/Dashboard.jsx';
import Contracts from './pages/Contracts.jsx';
import Tasks from './pages/Tasks.jsx';
import Calendar from './pages/Calendar.jsx';
import ReviewQueue from './pages/ReviewQueue.jsx';
import ContractAnalysis from './pages/ContractAnalysis.jsx';
import Notifications from './pages/Notifications.jsx';

// Human-friendly page titles, set on the browser tab so the user always knows where they are.
const PAGE_TITLES = [
  [/^\/login$/, 'Sign in'],
  [/^\/register$/, 'Create account'],
  [/^\/upload$/, 'Upload'],
  [/^\/contracts\/\d+/, 'Contract analysis'],
  [/^\/contracts$/, 'Analysis'],
  [/^\/tasks$/, 'Tasks'],
  [/^\/calendar$/, 'Calendar'],
  [/^\/review$/, 'Review queue'],
  [/^\/notifications$/, 'Notifications'],
  [/^\/$/, 'Dashboard'],
];

function useDocumentTitle() {
  const { pathname } = useLocation();
  useEffect(() => {
    const match = PAGE_TITLES.find(([re]) => re.test(pathname));
    document.title = match ? `${match[1]} · ClauseOps` : 'ClauseOps';
  }, [pathname]);
}

export default function App() {
  const initialize = useAuthStore((s) => s.initialize);
  useDocumentTitle();

  // On app start, attempt a silent refresh (via the httpOnly cookie) to restore a session.
  useEffect(() => {
    initialize();
  }, [initialize]);

  return (
    <Routes>
      {/* Public auth routes (branded two-pane layout) */}
      <Route element={<AuthLayout />}>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
      </Route>

      {/* Authenticated app (sidebar + top bar shell) */}
      <Route element={<ProtectedLayout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/contracts" element={<Contracts />} />
        <Route path="/contracts/:id" element={<ContractAnalysis />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/calendar" element={<Calendar />} />
        <Route path="/review" element={<ReviewQueue />} />
        <Route path="/notifications" element={<Notifications />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
