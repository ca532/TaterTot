import { FileText } from 'lucide-react';

function Navigation({ activeTab, setActiveTab }) {
  const tabs = [
    { id: 'summaries', label: 'Article Summaries', icon: FileText },
  ];

  return (
    <nav className="bg-white border-b-2 border-gray-200">
      <div className="w-full px-4 sm:px-6 lg:px-12">
        <div className="flex gap-4 sm:gap-8 overflow-x-auto">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`
                  flex items-center space-x-2 py-3 px-1 border-b-2 font-semibold text-xs sm:text-sm whitespace-nowrap
                  transition-colors duration-200
                  ${isActive 
                    ? 'border-[#b8860b] text-[#b8860b]' 
                    : 'border-transparent text-gray-500 hover:text-gray-900 hover:border-[#daa520]'
                  }
                `}
              >
                <Icon className="w-4 h-4" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}

export default Navigation;
