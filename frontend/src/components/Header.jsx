import { Circle, LogOut } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import logo from '../assets/logo-white-bg.png';  // Import the logo

function Header({ pipelineStatus }) {
  const { logout } = useAuth();
  
  const statusConfig = {
    idle: { color: 'text-gray-400', label: 'Ready' },
    running: { color: 'text-[#b8860b]', label: 'Running', pulse: true },
    complete: { color: 'text-[#b8860b]', label: 'Complete' },
  };

  const status = statusConfig[pipelineStatus] || statusConfig.idle;

  return (
    <header className="bg-white shadow-sm border-b-2 border-[#b8860b]">
      <div className="w-full px-4 sm:px-6 lg:px-12 py-3 sm:py-4">
        <div className="relative">
          {/* Logout Button - Positioned top right */}
          <button
            onClick={logout}
            className="absolute right-0 top-0 flex items-center gap-2 px-3 sm:px-4 py-2 text-xs sm:text-sm font-semibold text-red-600 hover:bg-red-50 rounded-lg transition-colors"
            title="Logout"
          >
            <LogOut className="w-4 h-4" />
            <span className="hidden sm:inline">Logout</span>
          </button>

          {/* Center content */}
          <div className="text-center">
            {/* Logo Image */}
            <div className="mb-2">
              <img 
                src={logo}
                alt="Claire Adler Luxury PR" 
                className="h-16 sm:h-20 lg:h-24 mx-auto"
              />
            </div>
            
            {/* Reading Roundup Dashboard */}
            <p className="text-xs sm:text-sm text-black font-medium mb-2">
              Reading Roundup Dashboard
            </p>
            
            {/* Status */}
            <div className="inline-flex items-center gap-2 px-3 py-1 bg-gray-50 rounded-full border border-gray-200">
              <Circle 
                className={`w-2 h-2 ${status.color} ${status.pulse ? 'animate-pulse' : ''} fill-current`} 
              />
              <span className="text-xs font-semibold text-gray-700">
                {status.label}
              </span>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}

export default Header;
