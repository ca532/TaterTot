import Header from './Header';
import Navigation from './Navigation';

function Layout({ children, activeTab, setActiveTab }) {
  return (
    <div className="min-h-screen bg-white flex flex-col overflow-hidden">
      <Header />
      <Navigation activeTab={activeTab} setActiveTab={setActiveTab} />
      
      {/* Scrollable content area */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden w-full px-4 sm:px-6 lg:px-12 py-3 sm:py-4">
        {children}
      </main>
    </div>
  );
}

export default Layout;
