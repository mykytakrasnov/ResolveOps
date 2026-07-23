import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router";

import { AppShell } from "@/components/app-shell";
import { CaseDetailPage } from "@/features/cases/case-detail-page";
import { CaseInboxPage } from "@/features/cases/case-inbox-page";
import { RunPage } from "@/features/runs/run-page";
import { ReviewPage } from "@/features/review/review-page";

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 30_000 },
      mutations: { retry: false },
    },
  });
}

const queryClient = createQueryClient();

export function AppRoutes() {
  return (
    <AppShell>
      <Routes>
        <Route path="/app/cases" element={<CaseInboxPage />} />
        <Route path="/app/cases/:caseId" element={<CaseDetailPage />} />
        <Route path="/app/runs/:runId" element={<RunPage />} />
        <Route path="/app/review" element={<ReviewPage />} />
        <Route path="*" element={<Navigate to="/app/cases" replace />} />
      </Routes>
    </AppShell>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRoutes />
    </QueryClientProvider>
  );
}
