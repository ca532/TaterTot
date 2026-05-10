import { useState } from 'react';
import { AuthProvider } from './contexts/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Layout from './components/Layout';
import SummariesView from './components/SummariesView';
import TrendAnalysisView from './components/TrendAnalysisView';
import './App.css';

function AppContent() {
  const [activeTab, setActiveTab] = useState('summaries');

  return (
    <div className="min-h-screen w-full">
      <Layout activeTab={activeTab} setActiveTab={setActiveTab}>
        {activeTab === 'summaries' && <SummariesView />}
        {activeTab === 'trends' && <TrendAnalysisView />}
      </Layout>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <ProtectedRoute>
        <AppContent />
      </ProtectedRoute>
    </AuthProvider>
  );
}

export default App;
