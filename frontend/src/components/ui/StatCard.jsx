function StatCard({ icon: Icon, label, value, hover = true }) {
  return (
    <div 
      className={`
        bg-white p-4 rounded-lg shadow-lg border-2 border-gray-200 
        ${hover ? 'hover:border-yellow-600 transition-colors' : ''}
      `}
    >
      <div className="flex items-center gap-2 mb-2">
        <Icon className="w-4 h-4 text-yellow-600" />
        <span className="text-xs font-semibold text-gray-600">{label}</span>
      </div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
    </div>
  );
}

export default StatCard;