import { Circle } from 'lucide-react';

function Header({ pipelineStatus }) {
  const statusConfig = {
    idle: { color: 'text-gray-400', label: 'Ready' },
    running: { color: 'text-[#b8860b]', label: 'Running', pulse: true },
    complete: { color: 'text-[#b8860b]', label: 'Complete' },
  };

  const status = statusConfig[pipelineStatus] || statusConfig.idle;

  return (
    <header className="bg-white shadow-sm border-b-2 border-[#b8860b]">
      <div className="w-full px-8 lg:px-16 py-4">
        <div className="text-center">
          {/* Logo Image - Made bigger */}
          <div className="mb-2">
            <img 
              src="/logo-white-bg.png" 
              alt="Claire Adler Luxury PR" 
              className="h-24 mx-auto"
            />
          </div>
          
          {/* TaterTot - Made smaller */}
          <h1 className="text-xl font-bold text-[#b8860b] mb-1">
            TaterTot
          </h1>
          <p className="text-sm text-black font-medium mb-2">
            A Reading Roundup Dashboard
          </p>
          
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
    </header>
  );
}

export default Header;