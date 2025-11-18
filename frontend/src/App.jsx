import { useState } from 'react';
import Layout from './components/Layout';
import SummariesView from './components/SummariesView';
import './App.css';

function App() {
  const [activeTab, setActiveTab] = useState('summaries');

  return (
    <Layout activeTab={activeTab} setActiveTab={setActiveTab}>
      {activeTab === 'summaries' && <SummariesView />}
    </Layout>
  );
}

export default App;