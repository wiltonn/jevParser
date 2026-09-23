import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Audit from "./pages/admin/Audit";
import JudgmentsAdmin from "./pages/admin/Judgments";
import RulesetEditor from "./pages/admin/RulesetEditor";
import Rulesets from "./pages/admin/Rulesets";
import SettingsPage from "./pages/admin/Settings";
import Users from "./pages/admin/Users";
import Catalog from "./pages/Catalog";
import CompareWizard from "./pages/CompareWizard";
import ComparisonList from "./pages/ComparisonList";
import ComparisonView from "./pages/ComparisonView";
import Dashboard from "./pages/Dashboard";
import DocumentPage from "./pages/DocumentPage";
import Jobs from "./pages/Jobs";
import Library from "./pages/Library";
import ModelPage from "./pages/ModelPage";
import TimelinePage from "./pages/TimelinePage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="compare" element={<CompareWizard />} />
        <Route path="comparisons" element={<ComparisonList />} />
        <Route path="comparisons/:id/*" element={<ComparisonView />} />
        <Route path="timelines/:id" element={<TimelinePage />} />
        <Route path="library" element={<Library />} />
        <Route path="library/:id" element={<DocumentPage />} />
        <Route path="catalog" element={<Catalog />} />
        <Route path="catalog/models/:id" element={<ModelPage />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="admin/rulesets" element={<Rulesets />} />
        <Route path="admin/rulesets/:id" element={<RulesetEditor />} />
        <Route path="admin/rulesets/:id/versions/:vid" element={<RulesetEditor />} />
        <Route path="admin/judgments" element={<JudgmentsAdmin />} />
        <Route path="admin/users" element={<Users />} />
        <Route path="admin/settings" element={<SettingsPage />} />
        <Route path="admin/audit" element={<Audit />} />
        <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
      </Route>
    </Routes>
  );
}
