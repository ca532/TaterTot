import { FileText, Clock, CheckCircle } from "lucide-react";

export default function StatsCards({ lastRunTime }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
      <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
        <div className="flex items-center gap-2 mb-1">
          <FileText className="w-4 h-4 text-[#b8860b]" />
          <span className="text-xs font-semibold text-gray-600">Publications</span>
        </div>
        <p className="text-xl font-bold text-gray-900">40</p>
      </div>

      <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
        <div className="flex items-center gap-2 mb-1">
          <Clock className="w-4 h-4 text-[#b8860b]" />
          <span className="text-xs font-semibold text-gray-600">Avg. Runtime</span>
        </div>
        <p className="text-xl font-bold text-gray-900">15-20 min</p>
      </div>

      <div className="bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 hover:border-[#b8860b] transition-colors">
        <div className="flex items-center gap-2 mb-1">
          <CheckCircle className="w-4 h-4 text-[#b8860b]" />
          <span className="text-xs font-semibold text-gray-600">Last Run</span>
        </div>
        <p className="text-xl font-bold text-gray-900">
          {lastRunTime ? lastRunTime.toLocaleDateString() : "Loading..."}
        </p>
      </div>
    </div>
  );
}

