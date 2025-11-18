import { CheckCircle, Loader2, Clock } from 'lucide-react';

function StepIndicator({ label, status = 'pending' }) {
  const statusConfig = {
    pending: {
      bg: 'bg-white border-2 border-gray-200',
      icon: Clock,
      iconColor: 'text-gray-400',
      textColor: 'text-gray-500',
    },
    active: {
      bg: 'bg-yellow-50 border-2 border-yellow-600',
      icon: Loader2,
      iconColor: 'text-yellow-600 animate-spin',
      textColor: 'text-gray-900',
    },
    complete: {
      bg: 'bg-green-50 border-2 border-green-500',
      icon: CheckCircle,
      iconColor: 'text-green-600',
      textColor: 'text-green-900',
    },
  };

  const config = statusConfig[status];
  const Icon = config.icon;

  return (
    <div className={`flex items-center gap-2 p-2 rounded-lg transition-all ${config.bg}`}>
      <Icon className={`w-4 h-4 ${config.iconColor} flex-shrink-0`} />
      <span className={`text-xs font-semibold ${config.textColor}`}>
        {label}
      </span>
    </div>
  );
}

export default StepIndicator;