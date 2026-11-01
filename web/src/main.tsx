import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono/400.css";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { AuthGate } from "./components/SignIn";
import { ToastProvider } from "./components/toasts";
import { PageHeader, Skeleton } from "./components/ui";
import "./index.css";
import Runs from "./pages/Runs";

const Compare = lazy(() => import("./pages/Compare"));
const Batches = lazy(() => import("./pages/Batches"));
const Batch = lazy(() => import("./pages/Batch"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Rubric = lazy(() => import("./pages/Rubric"));
const RubricDraft = lazy(() => import("./pages/RubricDraft"));
const RubricVersion = lazy(() => import("./pages/RubricVersion"));
const Import = lazy(() => import("./pages/Import"));
const Prompts = lazy(() => import("./pages/Prompts"));
const PromptEdit = lazy(() => import("./pages/PromptEdit"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthGate>{/* outside the toasts: their job poll needs a session */}
        <ToastProvider>
        <Shell>
          <Suspense fallback={<div className="p-8"><Skeleton className="h-64" /></div>}>
            <Routes>
              <Route path="/" element={<Runs />} />
              <Route path="/runs/:id" element={<Runs />} />
              <Route path="/compare" element={<Compare />} />
              <Route path="/prompts" element={<Prompts />} />
              <Route path="/prompts/:id" element={<PromptEdit />} />
              <Route path="/batches" element={<Batches />} />
              <Route path="/batches/:id" element={<Batch />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/rubric" element={<Rubric />} />
              <Route path="/rubric/drafts/:id" element={<RubricDraft />} />
              <Route path="/rubric/history/:file" element={<RubricVersion />} />
              <Route path="/rubric/active" element={<RubricVersion kind="active" />} />
              <Route path="/rubric/runs/:id" element={<RubricVersion kind="run" />} />
              <Route path="/import" element={<Import />} />
              <Route path="*" element={<main className="space-y-4 px-4 py-6 sm:px-8"><PageHeader title="Page not found" caption="This address doesn't match any page." /><a className="text-sky-600 underline" href="/">Back to runs</a></main>} />
            </Routes>
          </Suspense>
        </Shell>
        </ToastProvider>
      </AuthGate>
    </BrowserRouter>
  </StrictMode>,
);
